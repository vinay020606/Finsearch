package com.financialrag.gateway.dto;

import java.util.List;

public class SearchResultItemDto {
    private String chunk_id;
    private String doc_id;
    private String ticker_symbol;
    private String parent_section;
    private String content;
    private String chunk_type;
    private List<String> allowed_roles;
    private double rrf_score;
    private double rerank_score;

    public SearchResultItemDto() {}

    public String getChunk_id() { return chunk_id; }
    public void setChunk_id(String chunk_id) { this.chunk_id = chunk_id; }

    public String getDoc_id() { return doc_id; }
    public void setDoc_id(String doc_id) { this.doc_id = doc_id; }

    public String getTicker_symbol() { return ticker_symbol; }
    public void setTicker_symbol(String ticker_symbol) { this.ticker_symbol = ticker_symbol; }

    public String getParent_section() { return parent_section; }
    public void setParent_section(String parent_section) { this.parent_section = parent_section; }

    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }

    public String getChunk_type() { return chunk_type; }
    public void setChunk_type(String chunk_type) { this.chunk_type = chunk_type; }

    public List<String> getAllowed_roles() { return allowed_roles; }
    public void setAllowed_roles(List<String> allowed_roles) { this.allowed_roles = allowed_roles; }

    public double getRrf_score() { return rrf_score; }
    public void setRrf_score(double rrf_score) { this.rrf_score = rrf_score; }

    public double getRerank_score() { return rerank_score; }
    public void setRerank_score(double rerank_score) { this.rerank_score = rerank_score; }
}
