package com.financialrag.gateway.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import java.util.List;

public class SearchRequestDto {

    @NotBlank(message = "query is required")
    private String query;

    private List<String> user_roles = List.of("analyst");

    @Min(1)
    @Max(50)
    private int top_k = 10;

    public SearchRequestDto() {}

    public String getQuery() { return query; }
    public void setQuery(String query) { this.query = query; }

    public List<String> getUser_roles() { return user_roles; }
    public void setUser_roles(List<String> user_roles) { this.user_roles = user_roles; }

    public int getTop_k() { return top_k; }
    public void setTop_k(int top_k) { this.top_k = top_k; }
}
