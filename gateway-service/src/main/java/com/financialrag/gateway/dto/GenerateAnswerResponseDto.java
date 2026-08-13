package com.financialrag.gateway.dto;

import java.util.List;

public class GenerateAnswerResponseDto {
    private String query;
    private String humanized_answer;
    private int total_sources;
    private List<SearchResultItemDto> sources;

    public GenerateAnswerResponseDto() {}

    public String getQuery() { return query; }
    public void setQuery(String query) { this.query = query; }

    public String getHumanized_answer() { return humanized_answer; }
    public void setHumanized_answer(String humanized_answer) { this.humanized_answer = humanized_answer; }

    public int getTotal_sources() { return total_sources; }
    public void setTotal_sources(int total_sources) { this.total_sources = total_sources; }

    public List<SearchResultItemDto> getSources() { return sources; }
    public void setSources(List<SearchResultItemDto> sources) { this.sources = sources; }
}
