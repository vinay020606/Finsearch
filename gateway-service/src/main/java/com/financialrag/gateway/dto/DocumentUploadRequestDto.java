package com.financialrag.gateway.dto;

import jakarta.validation.constraints.NotBlank;
import java.util.List;

public class DocumentUploadRequestDto {

    @NotBlank(message = "doc_id is required")
    private String doc_id;

    @NotBlank(message = "ticker_symbol is required")
    private String ticker_symbol;

    @NotBlank(message = "filename is required")
    private String filename;

    @NotBlank(message = "content is required")
    private String content;

    private List<String> allowed_roles = List.of("admin", "analyst");

    public DocumentUploadRequestDto() {}

    public String getDoc_id() { return doc_id; }
    public void setDoc_id(String doc_id) { this.doc_id = doc_id; }

    public String getTicker_symbol() { return ticker_symbol; }
    public void setTicker_symbol(String ticker_symbol) { this.ticker_symbol = ticker_symbol; }

    public String getFilename() { return filename; }
    public void setFilename(String filename) { this.filename = filename; }

    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }

    public List<String> getAllowed_roles() { return allowed_roles; }
    public void setAllowed_roles(List<String> allowed_roles) { this.allowed_roles = allowed_roles; }
}
