package com.financialrag.gateway.controller;

import com.financialrag.gateway.dto.*;
import com.financialrag.gateway.service.RagService;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.Map;

@RestController
@CrossOrigin(origins = "*")
public class GatewayController {

    private final RagService ragService;

    public GatewayController(RagService ragService) {
        this.ragService = ragService;
    }

    @GetMapping(value = {"/", "/index.html", "/ui"})
    public ResponseEntity<String> serveIndexHtml() {
        return ResponseEntity.ok(ragService.getIndexHtml());
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

    @PostMapping(value = "/api/v1/documents/upload-file", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<DocumentUploadResponseDto> uploadDocumentFile(
            @RequestParam("file") MultipartFile file,
            @RequestParam("doc_id") String docId,
            @RequestParam("ticker_symbol") String tickerSymbol,
            @RequestParam(value = "allowed_roles", defaultValue = "admin,analyst") String allowedRoles) throws IOException {
        DocumentUploadResponseDto response = ragService.uploadDocumentFile(file, docId, tickerSymbol, allowedRoles);
        return ResponseEntity.status(201).body(response);
    }

    @PostMapping("/api/v1/search")
    public ResponseEntity<SearchResponseDto> searchDocuments(@Valid @RequestBody SearchRequestDto requestDto) {
        SearchResponseDto response = ragService.searchDocuments(requestDto);
        return ResponseEntity.ok(response);
    }

    @PostMapping("/api/v1/generate")
    public ResponseEntity<GenerateAnswerResponseDto> generateAnswer(@Valid @RequestBody GenerateAnswerRequestDto requestDto) {
        GenerateAnswerResponseDto response = ragService.generateAnswer(requestDto);
        return ResponseEntity.ok(response);
    }
}

