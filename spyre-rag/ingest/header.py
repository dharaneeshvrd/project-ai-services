from enum import Enum, auto
from typing import Any, Optional
from pdfminer.pdfdocument import PDFDocument, PDFNoOutlines
from pdfminer.pdfpage import PDFPage, LITERAL_PAGE
from pdfminer.pdfparser import PDFParser, PDFSyntaxError
from pdfminer.pdftypes import PDFObjRef
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer
from rapidfuzz import fuzz

class PDFRefType(Enum):
    """PDF reference type."""


    PDF_OBJ_REF = auto()
    DICTIONARY = auto()
    LIST = auto()
    NAMED_REF = auto()
    UNK = auto()  # fallback


class RefPageNumberResolver:
    """PDF Reference to page number resolver.

    .. note::

       Remote Go-To Actions (see 12.6.4.3 in
       `https://www.adobe.com/go/pdfreference/`__)
       are out of the scope of this resolver.

    Attributes:
        document (:obj:`pdfminer.pdfdocument.PDFDocument`):
            The document that contains the references.
        objid_to_pagenum (:obj:`dict[int, int]`):
            Mapping from an object id to the number of the page that contains
            that object.
    """


    def __init__(self, document: PDFDocument):
        self.document = document
        # obj_id -> page_number
        self.objid_to_pagenum: dict[int, int] = {
            page.pageid: page_num
            for page_num, page in enumerate(PDFPage.create_pages(document), 1)
        }

    @classmethod
    def get_ref_type(cls, ref: Any) -> PDFRefType:
        """Get the type of a PDF reference."""
        if isinstance(ref, PDFObjRef):
            return PDFRefType.PDF_OBJ_REF
        elif isinstance(ref, dict) and "D" in ref:
            return PDFRefType.DICTIONARY
        elif isinstance(ref, list) and any(isinstance(e, PDFObjRef) for e in ref):
            return PDFRefType.LIST
        elif isinstance(ref, bytes):
            return PDFRefType.NAMED_REF
        else:
            return PDFRefType.UNK


    @classmethod
    def is_ref_page(cls, ref: Any) -> bool:
        """Check whether a reference is of type '/Page'.


        Args:
            ref (:obj:`Any`):
                The PDF reference.

        Returns:
            :obj:`bool`: :obj:`True` if the reference references
            a page, :obj:`False` otherwise.
        """
        return isinstance(ref, dict) and "Type" in ref and ref["Type"] is LITERAL_PAGE

    def resolve(self, ref: Any) -> Optional[int]:
        """Resolve a PDF reference to a page number recursively.

        Args:
            ref (:obj:`Any`):
                The PDF reference.

        Returns:
            :obj:`Optional[int]`: The page number or :obj:`None`
            if the reference could not be resolved (e.g., remote Go-To
            Actions or malformed references).
        """
        ref_type = self.get_ref_type(ref)

        if ref_type is PDFRefType.PDF_OBJ_REF and self.is_ref_page(ref.resolve()):
            return self.objid_to_pagenum.get(ref.objid)
        elif ref_type is PDFRefType.PDF_OBJ_REF:
            return self.resolve(ref.resolve())

        if ref_type is PDFRefType.DICTIONARY:
            return self.resolve(ref["D"])

        if ref_type is PDFRefType.LIST:
            # Get the PDFObjRef in the list (usually first element).
            return self.resolve(next(filter(lambda e: isinstance(e, PDFObjRef), ref)))

        if ref_type is PDFRefType.NAMED_REF:
            return self.resolve(self.document.get_dest(ref))

        return None  # PDFRefType.UNK

def get_matching_header_lvl(toc, title, page_no, threshold=80):
    titleAndLevel = toc.get(page_no, None)
    if not titleAndLevel:
        return ""
    
    tocHeader = titleAndLevel["title"]
    print(f"title: {title}, tocHeader: {tocHeader}")
    score = fuzz.partial_ratio(title.lower(), tocHeader.lower())
    print(f"page content & title match score: {score}")
    if score >= threshold:
        return "#" * int(titleAndLevel["level"])
    return ""

def get_outlines(file: str):
    """Pretty print the outlines (ToC) of a PDF document."""
    toc = {}
    with open(file, "rb") as fp:
        try:
            parser = PDFParser(fp)
            document = PDFDocument(parser)

            ref_pagenum_resolver = RefPageNumberResolver(document)

            outlines = list(document.get_outlines())
            if not outlines:
                print("No outlines found.")
            for (level, title, dest, a, se) in outlines:
                if dest:
                    page_num = ref_pagenum_resolver.resolve(dest)
                elif a:
                    page_num = ref_pagenum_resolver.resolve(a)
                elif se:
                    page_num = ref_pagenum_resolver.resolve(se)
                else:
                    page_num = None
                page_num = page_num if page_num else -1
                toc[page_num] = {"level": level, "title": title}
            return toc
        except PDFNoOutlines:
            print("No outlines found.")
        except PDFSyntaxError:
            print("Corrupted PDF or non-PDF file.")
        finally:
            try:
                parser.close()
            except NameError:
                pass  # nothing to do
