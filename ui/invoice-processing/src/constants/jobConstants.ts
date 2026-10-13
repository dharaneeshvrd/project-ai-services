export const INVOICE_JOB_STATUS = {
  ACCEPTED: 'accepted',
  EXTRACTING: 'extracting',
  REVIEW: 'review',
  APPROVED: 'approved',
  REJECTED: 'rejected',
  FAILED: 'failed',
} as const;

export type InvoiceJobStatusType =
  (typeof INVOICE_JOB_STATUS)[keyof typeof INVOICE_JOB_STATUS];
