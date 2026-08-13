package com.financialrag.gateway.service;

import com.financialrag.gateway.dto.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

@Service
public class RagService {

    private final RestTemplate restTemplate;

    @Value("${python.service.url:http://app:8000}")
    private String pythonServiceUrl;

    public RagService(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public DocumentUploadResponseDto uploadDocument(DocumentUploadRequestDto requestDto) {
        String url = pythonServiceUrl + "/api/v1/documents/upload";
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);

        HttpEntity<DocumentUploadRequestDto> entity = new HttpEntity<>(requestDto, headers);
        ResponseEntity<DocumentUploadResponseDto> response = restTemplate.postForEntity(url, entity, DocumentUploadResponseDto.class);
        return response.getBody();
    }

    public SearchResponseDto searchDocuments(SearchRequestDto requestDto) {
        String url = pythonServiceUrl + "/api/v1/search";
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);

        HttpEntity<SearchRequestDto> entity = new HttpEntity<>(requestDto, headers);
        ResponseEntity<SearchResponseDto> response = restTemplate.postForEntity(url, entity, SearchResponseDto.class);
        return response.getBody();
    }

    public String checkPythonHealth() {
        try {
            String url = pythonServiceUrl + "/health";
            return restTemplate.getForObject(url, String.class);
        } catch (Exception e) {
            return "{\"status\": \"unhealthy\", \"error\": \"" + e.getMessage() + "\"}";
        }
    }
}
