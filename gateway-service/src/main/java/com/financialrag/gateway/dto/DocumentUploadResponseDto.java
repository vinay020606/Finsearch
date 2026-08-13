package com.financialrag.gateway.dto;

public class DocumentUploadResponseDto {
    private String doc_id;
    private String status;
    private int chunks_ingested;
    private String message;

    public DocumentUploadResponseDto() {}

    public String getDoc_id() { return doc_id; }
    public void setDoc_id(String doc_id) { this.doc_id = doc_id; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public int getChunks_ingested() { return chunks_ingested; }
    public void setChunks_ingested(int chunks_ingested) { this.chunks_ingested = chunks_ingested; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }
}
