package com.financialrag.gateway.dto;

import java.util.List;

public class SearchResponseDto {
    private String query;
    private int total_results;
    private List<SearchResultItemDto> results;

    public SearchResponseDto() {}

    public String getQuery() { return query; }
    public void setQuery(String query) { this.query = query; }

    public int getTotal_results() { return total_results; }
    public void setTotal_results(int total_results) { this.total_results = total_results; }

    public List<SearchResultItemDto> getResults() { return results; }
    public void setResults(List<SearchResultItemDto> results) { this.results = results; }
}
