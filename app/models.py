from pydantic import BaseModel, Field

class DocumentUploadRequest(BaseModel):
    doc_id: str = Field(..., description="Unique document ID", example="DOC-AAPL-10K-2024")
    ticker_symbol: str = Field(..., description="Stock Ticker Symbol", example="AAPL")
    filename: str = Field(..., description="Source filename", example="aapl_2024_10k.md")
    content: str = Field(..., description="Raw text, markdown, or HTML document content")
    allowed_roles: list[str] = Field(default_factory=lambda: ["admin", "analyst"], description="Allowed roles for RBAC access control")

class DocumentUploadResponse(BaseModel):
    doc_id: str
    status: str = Field(..., example="success")
    file_path: str
    chunks_ingested: int
    message: str

class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query string", example="What was Apple's total revenue in 2024?")
    user_roles: list[str] = Field(default_factory=lambda: ["analyst"], description="User roles for metadata RBAC filtering")
    top_k: int = Field(default=10, ge=1, le=50, description="Top K results to return")

class SearchResultItem(BaseModel):
    chunk_id: str
    doc_id: str
    ticker_symbol: str
    parent_section: str
    content: str
    chunk_type: str
    allowed_roles: list[str]
    rrf_score: float
    rerank_score: float

class SearchResponse(BaseModel):
    query: str
    total_results: int
    results: list[SearchResultItem]

class GenerateAnswerRequest(BaseModel):
    query: str = Field(..., description="Search query string", example="What was Apple's total revenue for the iPhone segment in 2024?")
    user_roles: list[str] = Field(default_factory=lambda: ["analyst"], description="User roles for RBAC metadata filtering")
    top_k: int = Field(default=5, ge=1, le=20, description="Top K context chunks to pass to LLM")

class GenerateAnswerResponse(BaseModel):
    query: str
    humanized_answer: str
    total_sources: int
    sources: list[SearchResultItem]
