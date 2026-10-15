import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import {
  Grid,
  Column,
  Breadcrumb,
  BreadcrumbItem,
  Button,
  InlineNotification,
  SkeletonText,
  Tag,
  Table,
  TableHead,
  TableHeader,
  TableBody,
  TableRow,
  TableCell,
  TableContainer,
  Tile,
  InlineLoading,
  Modal,
  Section,
  Heading,
} from '@carbon/react';
import { CheckmarkFilled, CloseFilled, ArrowLeft } from '@carbon/icons-react';
import { getJob, approveJob, rejectJob, InvoiceJob } from '../../services/api';

const HEADER_LABEL: Record<string, string> = {
  invoice_number: 'Invoice Number',
  invoice_date: 'Invoice Date',
  due_date: 'Due Date',
  vendor_name: 'Vendor Name',
  vendor_address: 'Vendor Address',
  bill_to_name: 'Bill To',
  bill_to_address: 'Bill To Address',
  subtotal: 'Subtotal',
  tax: 'Tax',
  total: 'Total',
  currency: 'Currency',
  payment_terms: 'Payment Terms',
  po_number: 'PO Number',
  notes: 'Notes',
};

function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
}

const ReviewPage: React.FC = () => {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();

  const [job, setJob] = useState<InvoiceJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<'approve' | 'reject' | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmModal, setConfirmModal] = useState<'approve' | 'reject' | null>(null);
  const [actionDone, setActionDone] = useState<'approved' | 'rejected' | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await getJob(jobId);
        if (!cancelled) { setJob(data); setError(null); }
      } catch (err: unknown) {
        if (!cancelled) {
          const msg =
            (err as { response?: { data?: { detail?: string } }; message?: string })?.response?.data?.detail ||
            (err as Error)?.message ||
            'Failed to load job.';
          setError(msg);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [jobId]);

  const handleAction = async (action: 'approve' | 'reject') => {
    if (!jobId) return;
    setConfirmModal(null);
    setActionLoading(action);
    setActionError(null);
    try {
      if (action === 'approve') {
        await approveJob(jobId);
      } else {
        await rejectJob(jobId);
      }
      setActionDone(action === 'approve' ? 'approved' : 'rejected');
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } }; message?: string })?.response?.data?.detail ||
        (err as Error)?.message ||
        `Failed to ${action} invoice.`;
      setActionError(msg);
    } finally {
      setActionLoading(null);
    }
  };

  const headerFields = job?.staged_header ? Object.entries(job.staged_header) : [];
  const lineItems: Record<string, unknown>[] = Array.isArray(job?.staged_lines) ? job!.staged_lines : [];
  const lineItemHeaders: string[] =
    lineItems.length > 0
      ? Object.keys(lineItems[0])
      : ['description', 'quantity', 'unit_price', 'amount'];

  return (
    <>
      {/* Modal must live outside the grid so it portals cleanly to body */}
      <Modal
        open={!!confirmModal}
        modalHeading={confirmModal === 'approve' ? 'Approve Invoice?' : 'Reject Invoice?'}
        primaryButtonText={confirmModal === 'approve' ? 'Approve' : 'Reject'}
        secondaryButtonText="Cancel"
        danger={confirmModal === 'reject'}
        onRequestClose={() => setConfirmModal(null)}
        onRequestSubmit={() => confirmModal && handleAction(confirmModal)}
      >
        <p>
          {confirmModal === 'approve'
            ? 'This will mark the invoice as approved and move it to the approved queue.'
            : 'This will reject the invoice. This action cannot be undone.'}
        </p>
      </Modal>

      <Grid className="cds--css-grid--full-width" style={{ padding: '2rem' }}>
        <Column lg={16} md={8} sm={4}>
          <Breadcrumb style={{ marginBottom: '1rem' }}>
            {/* href makes the item a real <a> for keyboard/a11y; onClick navigates via router */}
            <BreadcrumbItem
              href="/jobs"
              onClick={(e: React.MouseEvent) => { e.preventDefault(); navigate('/jobs'); }}
            >
              Pipeline Jobs
            </BreadcrumbItem>
            <BreadcrumbItem isCurrentPage>Review Invoice</BreadcrumbItem>
          </Breadcrumb>

          <div
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              justifyContent: 'space-between',
              marginBottom: '1.5rem',
              flexWrap: 'wrap',
              gap: '1rem',
            }}
          >
            {/* Use Section+Heading so the heading level is managed by Carbon's layout context */}
            <Section>
              <Heading className="cds--productive-heading-05">Review Invoice</Heading>
              <p className="cds--body-long-01" style={{ marginTop: '0.25rem' }}>
                Review extracted header fields, line items, and approve or reject the invoice.
              </p>
            </Section>
            <Button
              kind="ghost"
              renderIcon={ArrowLeft}
              iconDescription="Back to Jobs"
              onClick={() => navigate('/jobs')}
            >
              Back to Jobs
            </Button>
          </div>

          {error && (
            <div style={{ marginBottom: '1rem' }}>
              <InlineNotification
                kind="error"
                title="Error loading invoice."
                subtitle={error}
                onCloseButtonClick={() => setError(null)}
              />
            </div>
          )}

          {actionError && (
            <div style={{ marginBottom: '1rem' }}>
              <InlineNotification
                kind="error"
                title="Action failed."
                subtitle={actionError}
                onCloseButtonClick={() => setActionError(null)}
              />
            </div>
          )}

          {actionDone && (
            <div style={{ marginBottom: '1rem' }}>
              <InlineNotification
                kind={actionDone === 'approved' ? 'success' : 'info'}
                title={actionDone === 'approved' ? 'Invoice approved.' : 'Invoice rejected.'}
                subtitle="You can now navigate back to the jobs list."
              />
            </div>
          )}

          {loading ? (
            <div style={{ marginBottom: '2rem' }}>
              <SkeletonText paragraph lineCount={3} />
            </div>
          ) : job ? (
            <>
              {/* Metadata summary tile — use a div inside Tile for flex layout */}
              <Tile style={{ marginBottom: '1.5rem' }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2rem' }}>
                  <div>
                    <p className="cds--label-01" style={{ marginBottom: '0.25rem' }}>File</p>
                    <strong className="cds--body-short-02">{job.filename}</strong>
                  </div>
                  <div>
                    <p className="cds--label-01" style={{ marginBottom: '0.25rem' }}>Status</p>
                    <Tag type="purple" size="sm">{job.status}</Tag>
                  </div>
                  <div>
                    <p className="cds--label-01" style={{ marginBottom: '0.25rem' }}>Job ID</p>
                    <code className="cds--body-short-01">{job.job_id}</code>
                  </div>
                  <div>
                    <p className="cds--label-01" style={{ marginBottom: '0.25rem' }}>Submitted</p>
                    <span className="cds--body-short-01">
                      {job.submitted_at ? new Date(job.submitted_at).toLocaleString() : '—'}
                    </span>
                  </div>
                </div>
              </Tile>

              {/* Header fields */}
              <p className="cds--productive-heading-02" style={{ marginBottom: '0.75rem' }}>
                Extracted Header Fields
              </p>
              {headerFields.length === 0 ? (
                <p className="cds--body-long-01" style={{ marginBottom: '1.5rem' }}>
                  No extracted header data available.
                </p>
              ) : (
                <TableContainer style={{ marginBottom: '1.5rem' }}>
                  <Table size="sm" useZebraStyles>
                    <TableHead>
                      <TableRow>
                        <TableHeader>Field</TableHeader>
                        <TableHeader>Value</TableHeader>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {headerFields.map(([key, value]) => (
                        <TableRow key={key}>
                          <TableCell>
                            <strong>{HEADER_LABEL[key] ?? key}</strong>
                          </TableCell>
                          <TableCell>{formatFieldValue(value)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              )}

              {/* Line items */}
              <p className="cds--productive-heading-02" style={{ marginBottom: '0.75rem' }}>
                Line Items
              </p>
              {lineItems.length === 0 ? (
                <p className="cds--body-long-01" style={{ marginBottom: '2rem' }}>
                  No line items extracted.
                </p>
              ) : (
                <TableContainer style={{ marginBottom: '2rem' }}>
                  <Table size="sm" useZebraStyles>
                    <TableHead>
                      <TableRow>
                        {lineItemHeaders.map((h) => (
                          <TableHeader key={h}>
                            {HEADER_LABEL[h] ?? h.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                          </TableHeader>
                        ))}
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {lineItems.map((item, idx) => (
                        <TableRow key={idx}>
                          {lineItemHeaders.map((h) => (
                            <TableCell key={h}>{formatFieldValue(item[h])}</TableCell>
                          ))}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              )}

              {/* Approve / Reject actions */}
              {!actionDone && (
                <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
                  <Button
                    kind="primary"
                    renderIcon={CheckmarkFilled}
                    disabled={!!actionLoading}
                    onClick={() => setConfirmModal('approve')}
                  >
                    Approve Invoice
                  </Button>
                  <Button
                    kind="danger"
                    renderIcon={CloseFilled}
                    disabled={!!actionLoading}
                    onClick={() => setConfirmModal('reject')}
                  >
                    Reject Invoice
                  </Button>
                  {actionLoading && (
                    <InlineLoading
                      description={actionLoading === 'approve' ? 'Approving…' : 'Rejecting…'}
                    />
                  )}
                </div>
              )}
            </>
          ) : null}
        </Column>
      </Grid>
    </>
  );
};

export default ReviewPage;
