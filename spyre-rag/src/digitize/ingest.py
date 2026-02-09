from glob import glob
import json
import time
import logging
import os

from tqdm import tqdm
os.environ['GRPC_VERBOSITY'] = 'ERROR' 
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

from pathlib import Path
from docling.datamodel.document import DoclingDocument, TextItem
from concurrent.futures import as_completed, ProcessPoolExecutor, ThreadPoolExecutor
from sentence_splitter import SentenceSplitter

from common.llm_utils import create_llm_session, classify_text_with_llm, summarize_table
from common.misc_utils import get_logger, generate_file_checksum, text_suffix, table_suffix
from common.llm_utils import classify_text_with_llm, summarize_table
from common.misc_utils import *
from digitize.pdf_utils import *
from digitize.chunk import chunk_single_file, create_chunk_documents
from common.db_utils import MilvusVectorStore

logging.getLogger('docling').setLevel(logging.CRITICAL)

logger = get_logger("Docling")

LIGHT_PDF_POOL_SIZE = 4
HEAVY_PDF_POOL_SIZE = 2

HEAVY_PDF_PAGE_THRESHOLD = 500

is_debug = logger.isEnabledFor(logging.DEBUG) 
tqdm_wrapper = None
if is_debug:
    tqdm_wrapper = tqdm
else:
    tqdm_wrapper = lambda x, **kwargs: x

excluded_labels = {
    'page_header', 'page_footer', 'caption', 'reference', 'footnote'
}

POOL_SIZE = 32

create_llm_session(pool_maxsize=POOL_SIZE)

def process_converted_document(converted_json_path, pdf_path, out_path, conversion_stats, gen_model, gen_endpoint, emb_endpoint, max_tokens):    
    stem = Path(pdf_path).stem
    processed_text_json_path = (Path(out_path) / f"{stem}{text_suffix}")
    processed_table_json_path = (Path(out_path) / f"{stem}{table_suffix}")

    if conversion_stats["text_processed"] and conversion_stats["table_processed"]:
        logger.debug(f"Text & Table of {pdf_path} is processed already!")
        return pdf_path, processed_text_json_path, processed_table_json_path, 0, 0, {}

    try:
        timings = {}
        converted_doc = None
        page_count = 0
        table_count = 0

        logger.debug("Loading from converted json")

        converted_doc = DoclingDocument.load_from_json(Path(converted_json_path))
        if not converted_doc:
            raise Exception(f"failed to load converted json into Docling Document")

        if not conversion_stats["text_processed"]:

            # Initialize TocHeaders to get the Table of Contents (TOC)
            t0 = time.time()
            toc_headers = None
            try:
                toc_headers, page_count = get_toc(pdf_path)
            except Exception as e:
                logger.debug(f"No TOC found or failed to load TOC: {e}")

            # Load pdf pages one time when TOC headers not found for retrieving the font size of header texts
            pdf_pages = None
            if not toc_headers:
                pdf_pages = load_pdf_pages(pdf_path)
                page_count = len(pdf_pages)

            # --- Text Extraction ---
            structured_output = []
            last_header_level = 0
            for text_obj in tqdm_wrapper(converted_doc.texts, desc=f"Processing text content of '{pdf_path}'"):
                label = text_obj.label
                if label in excluded_labels:
                    continue

                # Check if it's a section header and process TOC or fallback to font size extraction
                if label == "section_header":
                    prov_list = text_obj.prov

                    for prov in prov_list:
                        page_no = prov.page_no

                        if toc_headers:
                            header_prefix = get_matching_header_lvl(toc_headers, text_obj.text)
                            if header_prefix:
                                # If TOC matches, use the level from TOC
                                structured_output.append({
                                    "label": label,
                                    "text": f"{header_prefix} {text_obj.text}",
                                    "page": page_no,
                                    "font_size": None,  # Font size isn't necessary if TOC matches
                                })
                                last_header_level = len(header_prefix.strip())  # Update last header level
                            else:
                                # If no match, use the previous header level + 1
                                new_header_level = last_header_level + 1
                                structured_output.append({
                                    "label": label,
                                    "text": f"{'#' * new_header_level} {text_obj.text}",
                                    "page": page_no,
                                    "font_size": None,  # Font size isn't necessary if TOC matches
                                })
                        else:
                            matches = find_text_font_size(pdf_pages, text_obj.text, page_no - 1)
                            if len(matches):
                                font_size = 0
                                count = 0
                                for match in matches:
                                    font_size += match["font_size"] if match["match_score"] == 100 else 0
                                    count += 1 if match["match_score"] == 100 else 0
                                font_size = font_size / count if count else None

                                structured_output.append({
                                    "label": label,
                                    "text": text_obj.text,
                                    "page": page_no,
                                    "font_size": round(font_size, 2) if font_size else None
                                })
                else:
                    structured_output.append({
                        "label": label,
                        "text": text_obj.text,
                        "page": text_obj.prov[0].page_no,
                        "font_size": None
                    })

            timings["process_text"] = time.time() - t0

            processed_text_json_path.write_text(json.dumps(structured_output, indent=2), encoding="utf-8")
            
        if not conversion_stats["table_processed"]:
            filtered_table_dicts = {}
            t0 = time.time()
            # --- Table Extraction ---
            table_count = len(converted_doc.tables)
            if converted_doc.tables:
                table_captions:TextItem = []
                for block in converted_doc.texts:
                    block_type = block.label
                    if block_type == 'caption':
                        block_parent = block.parent
                        if block_parent is not None and 'tables' in block_parent.cref:
                            table_captions.append(block)

                table_htmls_dict = {}
                table_captions_dict = {}
                for table_ix, table in enumerate(tqdm_wrapper(converted_doc.tables, desc=f"Processing table content of '{pdf_path}'")):
                    table_htmls_dict[table_ix] = table.export_to_html(doc=converted_doc)

                    for caption_idx, block in enumerate(table_captions):
                        if block.parent.cref == f'#/tables/{table_ix}':
                            table_captions_dict[table_ix] = block.text
                            table_captions.pop(caption_idx)
                            break

                table_htmls = [table_htmls_dict[key] for key in sorted(table_htmls_dict)]
                table_captions_list = [table_captions_dict[key] for key in sorted(table_captions_dict)]

                table_summaries = summarize_table(table_htmls, gen_model, gen_endpoint, pdf_path)

                decisions = classify_text_with_llm(table_summaries, gen_model, gen_endpoint, pdf_path)
                filtered_table_dicts = {
                    idx: {
                        'html': html,
                        'caption': caption,
                        'summary': summary
                    }
                    for idx, (keep, html, caption, summary) in enumerate(zip(decisions, table_htmls, table_captions_list, table_summaries)) if keep
                }
                processed_table_json_path.write_text(json.dumps(filtered_table_dicts, indent=2), encoding="utf-8")
                timings['process_tables'] = time.time() - t0

        return pdf_path, processed_text_json_path, processed_table_json_path, page_count, table_count, timings
    except Exception as e:
        logger.error(f"Error processing converted document for PDF: {pdf_path}. Details: {e}", exc_info=True)

        return None, None, None, None, None, None

def convert_document(pdf_path, conversion_stats, out_path):
    try:
        logger.info(f"Processing '{pdf_path}'")
        converted_json = (Path(out_path) / f"{Path(pdf_path).stem}.json")
        converted_json_f = str(converted_json)
        if not conversion_stats["convert"]:
            logger.debug(f"Checking {converted_json_f}")
            if converted_json.exists():
                logger.debug(f"Converted json exists already {converted_json_f}")
                return pdf_path, converted_json_f, 0.0

        logger.debug(f"Converting '{pdf_path}'")
        t0 = time.time()

        converted_doc = convert_doc(pdf_path)
        converted_doc.save_as_json(str(converted_json_f))

        conversion_time = time.time() - t0
        logger.debug(f"'{pdf_path}' converted")
        return pdf_path, converted_json_f, conversion_time
    except Exception as e:
        logger.error(f"Error converting '{pdf_path}': {e}")
    return None, None, None

def process_documents(input_paths, out_path, llm_model, llm_endpoint, emb_endpoint, max_tokens):
    # Skip files that already exist by matching the cached checksum of the pdf
    # if there is no difference in checksum and processed text & table json also exist, would skip for convert and process list
    # if checksum is matching but either processed text or table json not exist, process the file, but don't convert
    # else add the file to convert and process list(filtered_input_paths) 
    filtered_input_paths = {}
    converted_paths = []

    for path in input_paths:
        stem = Path(path).stem
        checksum_path = Path(out_path) / f"{stem}.checksum"
        filtered_input_paths[path] = {}
        filtered_input_paths[path]["text_processed"] = False
        filtered_input_paths[path]["table_processed"] = False
        filtered_input_paths[path]["chunked"] = False

        if not checksum_path.exists():
            filtered_input_paths[path]["convert"] = True
        else:
            cached_checksum = checksum_path.read_text().strip()
            new_checksum = generate_file_checksum(path)

            if cached_checksum != new_checksum:
                filtered_input_paths[path]["convert"] = True
            else:
                filtered_input_paths[path]["convert"] = False
                filtered_input_paths[path]["text_processed"] = (Path(out_path) / f"{stem}{text_suffix}").exists()
                filtered_input_paths[path]["table_processed"] = (Path(out_path) / f"{stem}{table_suffix}").exists()
                filtered_input_paths[path]["chunked"] = (Path(out_path) / f"{stem}{chunk_suffix}").exists()

    for path in filtered_input_paths:
        if filtered_input_paths[path]["convert"]:
            checksum = generate_file_checksum(path)
            (Path(out_path) / f"{Path(path).stem}.checksum").write_text(checksum, encoding='utf-8')

    light_files = {}
    heavy_files = {}

    for path, meta in filtered_input_paths.items():
        pg_count = get_pdf_page_count(path)
        if pg_count >= HEAVY_PDF_PAGE_THRESHOLD:
            heavy_files[path] = meta
        else:
            light_files[path] = meta

    logger.debug(f"Light files: {len(light_files)}, Heavy files: {len(heavy_files)}")

    def _run_batch(batch_paths, convert_worker, max_worker):
        batch_stats = {}
        batch_chunk_paths = []
        batch_table_paths = []
        
        if not batch_paths:
            return batch_stats, batch_chunk_paths, batch_table_paths

        with ProcessPoolExecutor(max_workers=convert_worker) as converter_executor, \
             ThreadPoolExecutor(max_workers=max_worker) as processor_executor, \
             ThreadPoolExecutor(max_workers=max_worker) as chunker_executor:

            # A. Submit Conversions
            conversion_futures = [
                converter_executor.submit(convert_document, path, batch_paths[path], out_path)
                for path in batch_paths
            ]
            
            process_futures = []
            chunk_futures = []

            # B. Handle Conversions -> Submit Processing
            for conversion_future in as_completed(conversion_futures):
                try:
                    path, converted_json, conversion_time = conversion_future.result()
                except Exception as e:
                    logger.error(f"Error from conversion: {e}")
                    continue
                
                if not converted_json:
                    continue
                
                converted_paths.append(path)
                batch_stats[path] = {"timings": {"conversion": conversion_time}}

                process_future = processor_executor.submit(
                    process_converted_document, converted_json, path, out_path, batch_paths[path], 
                    llm_model, llm_endpoint, emb_endpoint, max_tokens
                )
                process_futures.append(process_future)

            # C. Handle Processing -> Submit Chunking
            for process_future in as_completed(process_futures):
                try:
                    path, processed_text_json_path, processed_table_json_path, page_count, table_count, timings = process_future.result()
                except Exception as e:
                    logger.error(f"Error from processing: {e}")
                    continue

                if not processed_table_json_path:
                    continue

                batch_stats[path]["timings"].update(timings)
                batch_stats[path]["page_count"] = page_count
                batch_stats[path]["table_count"] = table_count
                batch_table_paths.append(processed_table_json_path)

                chunk_future = chunker_executor.submit(
                    chunk_single_file, processed_text_json_path, path, out_path, batch_paths[path], emb_endpoint, max_tokens
                )
                chunk_futures.append(chunk_future)

            # D. Handle Chunking
            for chunk_future in as_completed(chunk_futures):
                try:
                    processed_chunk_json_path, path, chunking_time = chunk_future.result()
                    batch_stats[path]["timings"]["chunking"] = chunking_time
                except Exception as e:
                    logger.error(f"Error from chunking: {e}")
                    continue

                if processed_chunk_json_path:
                    batch_chunk_paths.append(processed_chunk_json_path)
                    logger.info(f"Completed '{path}'")

        return batch_stats, batch_chunk_paths, batch_table_paths

    try:
        worker_size = min(LIGHT_PDF_POOL_SIZE, len(light_files))
        # Light files can be processed in parallel with worker_size
        l_stats, l_chunks, l_tables = _run_batch(
            light_files,
            convert_worker=worker_size,
            max_worker=worker_size,
        )

        worker_size = min(HEAVY_PDF_POOL_SIZE, len(heavy_files))
        # Heavy files processed with less workers compared to max_worker
        h_stats, h_chunks, h_tables = _run_batch(
            heavy_files,
            convert_worker=worker_size,
            max_worker=worker_size,
        )

        # Combine stats from both batches
        converted_pdf_stats = {**l_stats, **h_stats}
        all_chunk_json_paths = l_chunks + h_chunks
        all_table_json_paths = l_tables + h_tables

        combined_chunks = []
        succeeded_files = {**l_stats, **h_stats}.keys()
        
        for path in succeeded_files:
            stem = Path(path).stem
            c_path = Path(out_path) / f"{stem}{chunk_suffix}"
            t_path = Path(out_path) / f"{stem}{table_suffix}"
            
            if c_path in all_chunk_json_paths and t_path in all_table_json_paths:
                filtered_chunks = create_chunk_documents(c_path, t_path, path)
                combined_chunks.extend(filtered_chunks)

        return combined_chunks, converted_pdf_stats

    except Exception as e:
        logger.error(f"Pipeline Error: {e}")
        return None, None

def ingest(directory_path):

    def ingestion_failed():
        logger.info("❌ Ingestion failed, please re-run the ingestion again, If the issue still persists, please report an issue in https://github.com/IBM/project-ai-services/issues")

    logger.info(f"Ingestion started from dir '{directory_path}'")

    # Process each document in the directory
    allowed_file_types = {'pdf': b'%PDF'}
    input_file_paths = []
    total_pdfs = 0

    for path in glob(f'{directory_path}/**/*', recursive=True):
        if not has_allowed_extension(path, allowed_file_types):
            continue

        total_pdfs += 1 

        if is_supported_file(path,allowed_file_types):
            input_file_paths.append(path)
        else:
            logger.warning(
                f"Skipping file with .pdf extension but unsupported format: {path}"
            )
    
    file_cnt = len(input_file_paths)
    if not file_cnt > 0:
        logger.info(f"No documents found to process in '{directory_path}'")
        return

    logger.info(f"Processing {file_cnt} document(s)")

    emb_model_dict, llm_model_dict, _ = get_model_endpoints()
    # Initialize/reset the database before processing any files
    vector_store = MilvusVectorStore()
    collection_name = vector_store._generate_collection_name()
    
    out_path = setup_cache_dir(collection_name)

    start_time = time.time()
    combined_chunks, converted_pdf_stats = process_documents(
        input_file_paths, out_path, llm_model_dict['llm_model'], llm_model_dict['llm_endpoint'],  emb_model_dict["emb_endpoint"],
        max_tokens=emb_model_dict['max_tokens'] - 100)
    # converted_pdf_stats holds { file_name: {page_count: int, table_count: int, timings: {conversion: time_in_secs, process_text: time_in_secs, process_tables: time_in_secs, chunking: time_in_secs}} }
    if converted_pdf_stats is None or combined_chunks is None:
        ingestion_failed()
        return

    if combined_chunks:
        logger.info("Loading processed documents into DB")
        # Insert data into Milvus
        vector_store.insert_chunks(
            emb_model=emb_model_dict['emb_model'],
            emb_endpoint=emb_model_dict['emb_endpoint'],
            max_tokens=emb_model_dict['max_tokens'],
            chunks=combined_chunks
        )
        logger.info("Processed documents loaded into DB")

    # Log time taken for the file
    end_time = time.time()  # End the timer for the current file
    file_processing_time = end_time - start_time
    
    unprocessed_files = get_unprocessed_files(input_file_paths, converted_pdf_stats.keys())
    if len(unprocessed_files):
        logger.info(f"Ingestion completed partially, please re-run the ingestion again to ingest the following files.\n{"\n".join(unprocessed_files)}\nIf the issue still persists, please report an issue in https://github.com/IBM/project-ai-services/issues")
    else:
        logger.info(f"✅ Ingestion completed successfully, Time taken: {file_processing_time:.2f} seconds. You can query your documents via chatbot")
    
    ingested = file_cnt - len(unprocessed_files)
    percentage = (ingested / total_pdfs * 100) if total_pdfs else 0.0
    logger.info(
        f"Ingestion summary: {ingested}/{total_pdfs} files ingested "
        f"({percentage:.2f}% of total PDF files)"
    )

    # Print detailed stats
    total_pages = sum(converted_pdf_stats[file]["page_count"] for file in converted_pdf_stats)
    if not total_pages:
        # No pages were processed, ingestion must have done using cached data.
        return
    
    print("Stats of processed PDFs:")
    max_file_len = max(len(key) for key in converted_pdf_stats.keys())
    total_tables = sum(converted_pdf_stats[file]["table_count"] for file in converted_pdf_stats)
    total_time = 0
    header_format = f"| {"PDF":<{max_file_len}} | {"Total Pages":^{15}} | {"Total Tables":^{15}} |"
    if logger.isEnabledFor(logging.DEBUG):
        header_format += f" {"Conversion":^{15}} | {"Processing Text":^{15}} | {"Processing Tables":^{17}} | {"Chunking":^{15}} |"
    header_format += f" {"Total Time (s)":>{15}} |"

    print("-" * len(header_format))
    print(header_format)
    print("-" * len(header_format))
    for file in converted_pdf_stats:
        timings = converted_pdf_stats[file]["timings"]
        pdf_total_time = sum(timings.values())
        total_time += pdf_total_time
        if converted_pdf_stats[file]["page_count"] > 0:
            stats_to_print = f"| {file:<{max_file_len}} | {converted_pdf_stats[file]["page_count"]:^{15}} | {converted_pdf_stats[file]["table_count"]:^{15}} |"
            if logger.isEnabledFor(logging.DEBUG) :
                stats_to_print += f" {timings["conversion"]:^{15}.2f} | {timings["process_text"]:^{15}.2f} | {timings["process_tables"]:^{17}.2f} | {timings["chunking"]:^{15}.2f} |"
            stats_to_print += f" {pdf_total_time:>{15}.2f} |"
            print(stats_to_print)
    print("-" * len(header_format))
    footer = f"| {"Total":<{max_file_len}} | {total_pages:^{15}} | {total_tables:^{15}} |"
    print(footer)
    print("-" * len(footer))