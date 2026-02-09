import json
from pathlib import Path
from sentence_splitter import SentenceSplitter
import time
from tqdm import tqdm

from common.llm_utils import tokenize_with_llm
from common.misc_utils import chunk_suffix
from digitize.pdf_utils import *

is_debug = logger.isEnabledFor(logging.DEBUG) 
tqdm_wrapper = None
if is_debug:
    tqdm_wrapper = tqdm
else:
    tqdm_wrapper = lambda x, **kwargs: x

def collect_header_font_sizes(elements):
    """
    elements: list of dicts with at least keys: 'label', 'font_size'
    Returns a sorted list of unique section_header font sizes, descending.
    """
    sizes = {
        el['font_size']
        for el in elements
        if el.get('label') == 'section_header' and el.get('font_size') is not None
    }
    return sorted(sizes, reverse=True)

def get_header_level(text, font_size, sorted_font_sizes):
    """
    Determine header level based on markdown syntax or font size hierarchy.
    """
    text = text.strip()

    # Priority 1: Markdown syntax
    if text.startswith('#'):
        level = len(text.strip()) - len(text.strip().lstrip('#'))
        return level, text.strip().lstrip('#').strip()

    # Priority 2: Font size ranking
    try:
        level = sorted_font_sizes.index(font_size) + 1
    except ValueError:
        # Unknown font size → assign lowest priority
        level = len(sorted_font_sizes)

    return level, text


def count_tokens(text, emb_endpoint):
    token_len = len(tokenize_with_llm(text, emb_endpoint))
    return token_len

def split_text_into_token_chunks(text, emb_endpoint, max_tokens=512, overlap=50):
    sentences = SentenceSplitter(language='en').split(text)
    chunks = []
    current_chunk = []
    current_token_count = 0

    for sentence in sentences:
        token_len = count_tokens(sentence, emb_endpoint)

        if current_token_count + token_len > max_tokens:
            # save current chunk
            chunk_text = " ".join(current_chunk)
            chunks.append(chunk_text)
            # overlap logic (optional)
            if overlap > 0 and len(current_chunk) > 0:
                overlap_text = current_chunk[-1]
                current_chunk = [overlap_text]
                current_token_count = count_tokens(overlap_text, emb_endpoint)
            else:
                current_chunk = []
                current_token_count = 0

        current_chunk.append(sentence)
        current_token_count += token_len

    # flush last
    if current_chunk:
        chunk_text = " ".join(current_chunk)
        chunks.append(chunk_text)

    return chunks


def flush_chunk(current_chunk, chunks, emb_endpoint, max_tokens):
    content = current_chunk["content"].strip()
    if not content:
        return

    # Split content into token chunks
    token_chunks = split_text_into_token_chunks(content, emb_endpoint, max_tokens=max_tokens)

    for i, part in enumerate(token_chunks):
        chunk = {
            "chapter_title": current_chunk["chapter_title"],
            "section_title": current_chunk["section_title"],
            "subsection_title": current_chunk["subsection_title"],
            "subsubsection_title": current_chunk["subsubsection_title"],
            "content": part,
            "page_range": sorted(set(current_chunk["page_range"])),
            "source_nodes": current_chunk["source_nodes"].copy()
        }
        if len(token_chunks) > 1:
            chunk["part_id"] = i + 1
        chunks.append(chunk)

    # Reset current_chunk after flushing
    current_chunk["chapter_title"] = ""
    current_chunk["section_title"] = ""
    current_chunk["subsection_title"] = ""
    current_chunk["subsubsection_title"] = ""
    current_chunk["content"] = ""
    current_chunk["page_range"] = []
    current_chunk["source_nodes"] = []


def chunk_single_file(input_path, pdf_path, out_path, conversion_stats, emb_endpoint, max_tokens=512):
    t0 = time.time()
    stem = Path(pdf_path).stem
    processed_chunk_json_path = (Path(out_path) / f"{stem}{chunk_suffix}")

    if conversion_stats["chunked"]:
        logger.debug(f"{pdf_path} already chunked!")
        return processed_chunk_json_path, pdf_path, 0.0

    try:
        if not Path(processed_chunk_json_path).exists():
            with open(input_path, "r") as f:
                data = json.load(f)
            
            font_size_levels = collect_header_font_sizes(data)

            chunks = []
            current_chunk = {
                "chapter_title": None,
                "section_title": None,
                "subsection_title": None,
                "subsubsection_title": None,
                "content": "",
                "page_range": [],
                "source_nodes": []
            }

            current_chapter = None
            current_section = None
            current_subsection = None
            current_subsubsection = None

            for idx, block in enumerate(tqdm_wrapper(data, desc=f"Chunking {input_path}")):
                label = block.get("label")
                text = block.get("text", "").strip()
                try:
                    page_no = block.get("prov", {})[0].get("page_no")
                except:
                    page_no = 0
                ref = f"#texts/{idx}"

                if label == "section_header":
                    level, full_title = get_header_level(text, block.get("font_size"), font_size_levels)
                    if level == 1:
                        current_chapter = full_title
                        current_section = None
                        current_subsection = None
                        current_subsubsection = None
                    elif level == 2:
                        current_section = full_title
                        current_subsection = None
                        current_subsubsection = None
                    elif level == 3:
                        current_subsection = full_title
                        current_subsubsection = None
                    else:
                        current_subsubsection = full_title

                    # Flush current chunk and update
                    flush_chunk(current_chunk, chunks, emb_endpoint, max_tokens)
                    current_chunk["chapter_title"] = current_chapter
                    current_chunk["section_title"] = current_section
                    current_chunk["subsection_title"] = current_subsection
                    current_chunk["subsubsection_title"] = current_subsubsection

                elif label in {"text", "list_item", "code", "formula"}:
                    if current_chunk["chapter_title"] is None:
                        current_chunk["chapter_title"] = current_chapter
                    if current_chunk["section_title"] is None:
                        current_chunk["section_title"] = current_section
                    if current_chunk["subsection_title"] is None:
                        current_chunk["subsection_title"] = current_subsection
                    if current_chunk["subsubsection_title"] is None:
                        current_chunk["subsubsection_title"] = current_subsubsection

                    if label == 'code':
                        current_chunk["content"] += f"```\n{text}\n``` "
                    elif label == 'formula':
                        current_chunk["content"] += f"${text}$ "
                    else:
                        current_chunk["content"] += f"{text} "
                    if page_no is not None:
                        current_chunk["page_range"].append(page_no)
                    current_chunk["source_nodes"].append(ref)
                else:
                    logger.debug(f'Skipping adding "{label}".')

            # Flush any remaining content
            flush_chunk(current_chunk, chunks, emb_endpoint, max_tokens)

            # Save the processed chunks to the output file
            with open(processed_chunk_json_path, "w") as f:
                json.dump(chunks, f, indent=2)

            logger.debug(f"{len(chunks)} RAG chunks saved to {processed_chunk_json_path}")
        else:
            logger.debug(f"{processed_chunk_json_path} already exists.")
        return processed_chunk_json_path, pdf_path, time.time() - t0
    except Exception as e:
        logger.error(f"error chunking file '{input_path}': {e}")
    return None, None, None

def create_chunk_documents(in_txt_f, in_tab_f, orig_fn):
    logger.debug(f"Creating combined chunk documents from '{in_txt_f}' & '{in_tab_f}'")
    with open(in_txt_f, "r") as f:
        txt_data = json.load(f)

    with open(in_tab_f, "r") as f:
        tab_data = json.load(f)

    txt_docs = []
    if len(txt_data):
        for _, block in enumerate(txt_data):
            meta_info = ''
            if block.get('chapter_title'):
                meta_info += f"Chapter: {block.get('chapter_title')} "
            if block.get('section_title'):
                meta_info += f"Section: {block.get('section_title')} "
            if block.get('subsection_title'):
                meta_info += f"Subsection: {block.get('subsection_title')} "
            if block.get('subsubsection_title'):
                meta_info += f"Subsubsection: {block.get('subsubsection_title')} "
            txt_docs.append({
                # "chunk_id": txt_id,
                "page_content": f'{meta_info}\n{block.get("content")}' if meta_info != '' else block.get("content"),
                "filename": orig_fn,
                "type": "text",
                "source": meta_info,
                "language": "en"
            })

    tab_docs = []
    if len(tab_data):
        tab_data = list(tab_data.values())
        for tab_id, block in enumerate(tab_data):
            # tab_docs.append(Document(
            #     page_content=block.get('summary'),
            #     metadata={"filename": orig_fn, "type": "table", "source": block.get('html'), "chunk_id": tab_id}
            # ))
            tab_docs.append({
                "page_content": block.get("summary"),
                "filename": orig_fn,
                "type": "table",
                "source": block.get("html"),
                "language": "en"
            })

    combined_docs = txt_docs + tab_docs

    logger.debug(f"Combined chunk documents created")

    return combined_docs