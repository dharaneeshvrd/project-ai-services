export const INVOICE_JOB_STATUS = {
  ACCEPTED: 'accepted',
  ROUTING: 'routing',
  DIGITIZING: 'digitizing',
  EXTRACTING: 'extracting',
  STAGING: 'staging',
  REVIEW: 'review',
  COMPLETED: 'completed',
  REJECTED: 'rejected',
  FAILED: 'failed',
} as const;

export type InvoiceJobStatusType =
  (typeof INVOICE_JOB_STATUS)[keyof typeof INVOICE_JOB_STATUS];
