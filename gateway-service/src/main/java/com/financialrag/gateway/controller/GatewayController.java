package com.financialrag.gateway.controller;

import com.financialrag.gateway.dto.*;
import com.financialrag.gateway.service.RagService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@CrossOrigin(origins = "*")
public class GatewayController {

    private final RagService ragService;

    public GatewayController(RagService ragService) {
        this.ragService = ragService;
    }

    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> healthCheck() {
        String pythonHealth = ragService.checkPythonHealth();
        return ResponseEntity.ok(Map.of(
            "gateway_status", "healthy",
            "python_service", pythonHealth
        ));
    }

    @PostMapping("/api/v1/documents/upload")
    public ResponseEntity<DocumentUploadResponseDto> uploadDocument(@Valid @RequestBody DocumentUploadRequestDto requestDto) {
        DocumentUploadResponseDto response = ragService.uploadDocument(requestDto);
        return ResponseEntity.status(201).body(response);
    }

    @PostMapping("/api/v1/search")
    public ResponseEntity<SearchResponseDto> searchDocuments(@Valid @RequestBody SearchRequestDto requestDto) {
        SearchResponseDto response = ragService.searchDocuments(requestDto);
        return ResponseEntity.ok(response);
    }
}
