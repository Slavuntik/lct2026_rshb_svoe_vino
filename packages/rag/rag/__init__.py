"""RAG-ядро «Свой Сомелье»: ingest, гибридный retrieval, resolve этикеток,
«аналог импортного», eval-контур. См. contracts/rag-interface.md.
"""
from rag.base import Candidate, Filters, Retriever, get_retriever

__all__ = ["Retriever", "Filters", "Candidate", "get_retriever"]
