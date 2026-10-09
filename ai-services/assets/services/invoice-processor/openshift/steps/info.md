Day N:

{{- if ne .UI_URL "" }}
{{- if eq .UI_STATUS "running" }}

- Process invoices using the {{ .SERVICE_NAME }} UI: {{ .UI_URL }}.
{{- else }}

- {{ .SERVICE_NAME }} UI is unavailable to use. Please make sure the 'invoice-processing-ui' pod is running.
{{- end }}
{{- end }}

{{- if ne .API_URL "" }}
{{- if eq .API_STATUS "running" }}

- {{ .SERVICE_NAME }} API is available to use at {{ .API_URL }}. Use this endpoint for programmatic invoice submission and status polling.
{{- else }}

- {{ .SERVICE_NAME }} API is unavailable to use. Please make sure the 'invoice-processing-api' pod is running.
{{- end }}
{{- end }}
