package com.financialrag.gateway.service;

import com.financialrag.gateway.dto.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;

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

    public DocumentUploadResponseDto uploadDocumentFile(MultipartFile file, String docId, String tickerSymbol, String allowedRoles) throws IOException {
        String url = pythonServiceUrl + "/api/v1/documents/upload-file";
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);

        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        ByteArrayResource fileResource = new ByteArrayResource(file.getBytes()) {
            @Override
            public String getFilename() {
                return file.getOriginalFilename() != null ? file.getOriginalFilename() : "document.txt";
            }
        };

        body.add("file", fileResource);
        body.add("doc_id", docId);
        body.add("ticker_symbol", tickerSymbol);
        body.add("allowed_roles", allowedRoles);

        HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body, headers);
        ResponseEntity<DocumentUploadResponseDto> response = restTemplate.postForEntity(url, requestEntity, DocumentUploadResponseDto.class);
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

    public GenerateAnswerResponseDto generateAnswer(GenerateAnswerRequestDto requestDto) {
        String url = pythonServiceUrl + "/api/v1/generate";
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);

        HttpEntity<GenerateAnswerRequestDto> entity = new HttpEntity<>(requestDto, headers);
        ResponseEntity<GenerateAnswerResponseDto> response = restTemplate.postForEntity(url, entity, GenerateAnswerResponseDto.class);
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

    public String getIndexHtml() {
        try {
            String url = pythonServiceUrl + "/";
            return restTemplate.getForObject(url, String.class);
        } catch (Exception e) {
            return "<html><body><h1>Financial RAG Gateway</h1><p>Python Service Error: " + e.getMessage() + "</p></body></html>";
        }
    }
}


