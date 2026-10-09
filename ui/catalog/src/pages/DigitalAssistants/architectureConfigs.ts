/**
 * Per-architecture presentation settings for the architecture-driven page
 * (deployments table, About tab, deploy flow, deployment details).
 */
export interface ArchitecturePageConfig {
  /** Catalog architecture ID, e.g. "rag". */
  architectureId: string;
  /** Singular lower-case name used in labels, toasts and empty states. */
  entityLabel: string;
  /** Page title and breadcrumb label (used until the catalog title is loaded). */
  fallbackTitle: string;
  /** Page subtitle shown until the catalog description is loaded. */
  fallbackSubtitle: string;
  /** Catalog ID of the member service whose `ui` endpoint is launched. */
  launchServiceId: string;
  /** Human-readable name of the launch target, used in error messages. */
  launchTargetLabel: string;
}

export const DIGITAL_ASSISTANTS_CONFIG: ArchitecturePageConfig = {
  architectureId: "rag",
  entityLabel: "digital assistant",
  fallbackTitle: "Digital assistants",
  fallbackSubtitle:
    "Production-ready tools that help users complete tasks and access information through conversation or commands. Assistants integrate multiple services for complex use cases and support retrieval-augmented generation (RAG).",
  launchServiceId: "chat",
  launchTargetLabel: "chatbot",
};

export const INVOICE_PROCESSING_CONFIG: ArchitecturePageConfig = {
  architectureId: "invoice-processing",
  entityLabel: "deployment",
  fallbackTitle: "Invoice processing",
  fallbackSubtitle:
    "Automates end-to-end invoice handling by digitizing PDF and image invoices, extracting structured data with an LLM, and loading the results into a target ERP or database system.",
  launchServiceId: "invoice-processor",
  launchTargetLabel: "invoice processing",
};
