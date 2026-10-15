import React, { useEffect, useState, useRef, useId } from 'react';
import { useParams, useNavigate } from 'react-router';
import {
  Breadcrumb,
  BreadcrumbItem,
  Button,
  Column,
  Grid,
  InlineLoading,
  InlineNotification,
  Layer,
  Modal,
  ProgressIndicator,
  ProgressStep,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
  TextArea,
  TextInput,
  Tile,
} from '@carbon/react';
import {
  Checkmark,
  Close,
  ChevronDown,
  ChevronUp,
  ZoomIn,
  ZoomOut,
  Reset,
  Download,
  ArrowLeft,
} from '@carbon/icons-react';
import {
  getJob,
  updateJob,
  approveJob,
  rejectJob,
  fetchInvoiceBlob,
  InvoiceJob,
} from '../../services/api';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PIPELINE_STEPS = ['Accepted', 'Extracting', 'Review', 'Approved'];

const STEP_INDEX: Record<string, number> = {
  accepted: 0,
  extracting: 1,
  review: 2,
  approved: 3,
  rejected: 2,
  failed: 1,
};

// Fields the reviewer may edit inline when the job is in review status
const EDITABLE_HEADER_KEYS = new Set([
  'INVOICE_NUM', 'INVOICE_DATE', 'INVOICE_TYPE_LOOKUP_CODE', 'VENDOR_NAME',
  'VENDOR_NUM', 'SELLER', 'SELLER_ADDRESS', 'CLIENT', 'CLIENT_ADDRESS',
  'INVOICE_AMOUNT', 'INVOICE_CURRENCY_CODE', 'TAX_AMOUNT', 'TOTAL_TAX_AMOUNT',
  'FREIGHT_AMOUNT', 'TERMS_NAME', 'DESCRIPTION',
]);

// Columns shown in the lines table
const LINE_COLS: { key: string; label: string }[] = [
  { key: 'LINE_NUMBER',        label: '#'           },
  { key: 'DESCRIPTION',        label: 'Description'  },
  { key: 'QUANTITY_INVOICED',  label: 'Qty'          },
  { key: 'UNIT_PRICE',         label: 'Unit Price'   },
  { key: 'AMOUNT',             label: 'Amount'       },
  { key: 'TAX_CODE',           label: 'Tax Code'     },
  { key: 'PO_NUMBER',          label: 'PO #'         },
];

const HEADER_LABEL: Record<string, string> = {
  INVOICE_NUM: 'Invoice Number',
  INVOICE_DATE: 'Invoice Date',
  INVOICE_TYPE_LOOKUP_CODE: 'Invoice Type',
  VENDOR_NAME: 'Vendor Name',
  VENDOR_NUM: 'Vendor Number',
  VENDOR_ID: 'Vendor ID',
  VENDOR_SITE_ID: 'Vendor Site ID',
  VENDOR_SITE_CODE: 'Vendor Site Code',
  INVOICE_AMOUNT: 'Invoice Amount',
  INVOICE_CURRENCY_CODE: 'Currency',
  EXCHANGE_RATE: 'Exchange Rate',
  EXCHANGE_RATE_TYPE: 'Exchange Rate Type',
  EXCHANGE_DATE: 'Exchange Date',
  TERMS_ID: 'Terms ID',
  TERMS_NAME: 'Terms Name',
  DESCRIPTION: 'Description',
  SOURCE: 'Source',
  ORG_ID: 'Org ID',
  OPERATING_UNIT: 'Operating Unit',
  PAY_GROUP_LOOKUP_CODE: 'Pay Group',
  PAYMENT_METHOD_LOOKUP_CODE: 'Payment Method',
  TAX_AMOUNT: 'Tax Amount',
  TOTAL_TAX_AMOUNT: 'Total Tax Amount',
  FREIGHT_AMOUNT: 'Freight Amount',
  SELLER: 'Seller',
  SELLER_ADDRESS: 'Seller Address',
  CLIENT: 'Client',
  CLIENT_ADDRESS: 'Client Address',
  // Metadata fields (prefixed with _ by the pipeline, always shown last)
  _job_id:      'Job ID',
  _source_file: 'Source File',
};

const ZOOM_STEP = 25;
const ZOOM_MIN  = 50;
const ZOOM_MAX  = 300;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(val: unknown): React.ReactNode {
  if (val === null || val === undefined || val === '') {
    return <span style={{ color: 'var(--cds-text-placeholder)' }}>—</span>;
  }
  return String(val);
}

// ---------------------------------------------------------------------------
// EditableCell — click-to-edit, saves on blur or Enter
// ---------------------------------------------------------------------------
interface EditableCellProps {
  value: unknown;
  onSave: (v: string) => void;
  disabled: boolean;
}

function EditableCell({ value, onSave, disabled }: EditableCellProps) {
  const [editing, setEditing] = useState(false);
  const [draft,   setDraft]   = useState(String(value ?? ''));
  const inputRef              = useRef<HTMLInputElement>(null);
  const inputId               = useId();

  function startEdit() {
    if (disabled) return;
    // Always sync draft from latest prop value when starting an edit
    setDraft(String(value ?? ''));
    setEditing(true);
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  function commit() {
    setEditing(false);
    const trimmed = draft.trim();
    if (trimmed !== String(value ?? '').trim()) onSave(trimmed);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter')  { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { setEditing(false); setDraft(String(value ?? '')); }
  }

  if (editing) {
    return (
      <TextInput
        ref={inputRef}
        id={inputId}
        labelText=""
        hideLabel
        size="sm"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={onKeyDown}
        style={{ minWidth: '6rem' }}
      />
    );
  }

  return (
    <span
      onClick={startEdit}
      title={disabled ? undefined : 'Click to edit'}
      style={{
        display: 'inline-block',
        minWidth: '4rem',
        cursor: disabled ? 'default' : 'text',
        padding: '0.1rem 0.25rem',
        borderRadius: 2,
        border: '1px dashed transparent',
        transition: 'border-color 0.15s',
      }}
      onMouseEnter={(e) => { if (!disabled) (e.currentTarget as HTMLElement).style.borderColor = 'var(--cds-border-subtle)'; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'transparent'; }}
    >
      {fmt(value)}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ReviewPage
// ---------------------------------------------------------------------------
const ReviewPage: React.FC = () => {
  const { jobId }  = useParams<{ jobId: string }>();
  const navigate   = useNavigate();

  const [job,      setJob]      = useState<InvoiceJob | null>(null);
  const [loading,  setLoading]  = useState(true);
  const [notice,   setNotice]   = useState<{ kind: 'error' | 'success' | 'info' | 'warning'; title: string; subtitle?: string } | null>(null);
  const [acting,   setActing]   = useState(false);

  // File viewer
  const [blobUrl,          setBlobUrl]          = useState<string | null>(null);
  const [viewerExpanded,   setViewerExpanded]   = useState(true);
  const [zoom,             setZoom]             = useState(100);
  const blobRef = useRef<string | null>(null);

  // Reject modal
  const [rejectOpen,   setRejectOpen]   = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  // Load job data
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await getJob(jobId);
        if (!cancelled) { setJob(data); }
      } catch (err: unknown) {
        if (!cancelled) {
          setNotice({
            kind: 'error',
            title: 'Failed to load job.',
            subtitle: (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
              ?? (err as Error).message,
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [jobId]);

  // Fetch invoice file as blob for the viewer
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    // Revoke any stale blob — do not call setBlobUrl here, do it in the async callback
    if (blobRef.current) { URL.revokeObjectURL(blobRef.current); blobRef.current = null; }
    fetchInvoiceBlob(jobId)
      .then((url) => {
        if (cancelled) { URL.revokeObjectURL(url); return; }
        blobRef.current = url;
        setBlobUrl(null); // clear old first, then set new
        setBlobUrl(url);
      })
      .catch(() => { /* viewer stays empty; no hard error */ });
    return () => {
      cancelled = true;
      if (blobRef.current) { URL.revokeObjectURL(blobRef.current); blobRef.current = null; }
    };
  }, [jobId]);

  // ---------------------------------------------------------------------------
  // Inline header field save — PATCHes the whole staged_header
  // ---------------------------------------------------------------------------
  async function saveHeaderField(key: string, value: string) {
    if (!job || !jobId) return;
    const updated = { ...(job.staged_header ?? {}), [key]: value };
    try {
      const saved = await updateJob(jobId, { staged_header: updated });
      setJob(saved);
      setNotice({ kind: 'success', title: `"${HEADER_LABEL[key] ?? key}" updated.` });
    } catch (err: unknown) {
      setNotice({
        kind: 'error',
        title: 'Update failed.',
        subtitle: (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
          ?? (err as Error).message,
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Approve
  // ---------------------------------------------------------------------------
  async function handleApprove() {
    if (!jobId) return;
    setActing(true);
    setNotice(null);
    try {
      await approveJob(jobId);
      setNotice({ kind: 'success', title: 'Invoice approved.', subtitle: 'Returning to jobs list…' });
      setTimeout(() => navigate('/jobs'), 2000);
    } catch (err: unknown) {
      setNotice({
        kind: 'error',
        title: 'Approval failed.',
        subtitle: (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
          ?? (err as Error).message,
      });
    } finally {
      setActing(false);
    }
  }

  // ---------------------------------------------------------------------------
  // Reject
  // ---------------------------------------------------------------------------
  async function handleReject() {
    if (!jobId || !rejectReason.trim()) return;
    setActing(true);
    setNotice(null);
    try {
      await rejectJob(jobId);
      setNotice({ kind: 'warning', title: 'Invoice rejected.', subtitle: 'Returning to jobs list…' });
      setRejectOpen(false);
      setTimeout(() => navigate('/jobs'), 2000);
    } catch (err: unknown) {
      setNotice({
        kind: 'error',
        title: 'Rejection failed.',
        subtitle: (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
          ?? (err as Error).message,
      });
    } finally {
      setActing(false);
    }
  }

  // ---------------------------------------------------------------------------
  // Derived values
  // ---------------------------------------------------------------------------
  const isReviewable = job?.status?.toLowerCase() === 'review';
  const currentStep  = STEP_INDEX[job?.status?.toLowerCase() ?? ''] ?? 0;
  const isPdf        = (job?.filename ?? '').toLowerCase().endsWith('.pdf');

  const headerRows = job?.staged_header
    ? Object.entries(job.staged_header)
        .sort(([a], [b]) => {
          const aMeta = a.startsWith('_');
          const bMeta = b.startsWith('_');
          if (aMeta !== bMeta) return aMeta ? 1 : -1;
          return 0;
        })
    : [];

  const lineItems: Record<string, unknown>[] = Array.isArray(job?.staged_lines)
    ? job!.staged_lines
    : [];

  // Download URL — direct endpoint (no auth needed for this service)
  const downloadUrl = `/v1/invoices/${jobId}/file`;

  // ---------------------------------------------------------------------------
  if (loading) {
    return (
      <div style={{ padding: '2rem' }}>
        <InlineLoading description="Loading invoice data…" />
      </div>
    );
  }

  return (
    <>
      {/* Reject modal */}
      <Modal
        open={rejectOpen}
        danger
        modalHeading="Reject Invoice"
        primaryButtonText={acting ? 'Rejecting…' : 'Confirm Rejection'}
        secondaryButtonText="Cancel"
        primaryButtonDisabled={acting || !rejectReason.trim()}
        onRequestSubmit={handleReject}
        onRequestClose={() => setRejectOpen(false)}
        size="sm"
      >
        <Stack gap={5}>
          <p className="cds--body-long-01">
            Provide a reason for rejection. This cannot be undone.
          </p>
          <TextArea
            id="reject-reason"
            labelText="Rejection reason"
            placeholder="e.g. Duplicate invoice, PO number mismatch…"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            rows={4}
          />
        </Stack>
      </Modal>

      <Grid className="cds--css-grid--full-width" style={{ padding: '2rem' }}>
        <Column lg={16} md={8} sm={4}>

          {/* Breadcrumb */}
          <Breadcrumb style={{ marginBottom: '1rem' }}>
            <BreadcrumbItem
              href="/jobs"
              onClick={(e: React.MouseEvent) => { e.preventDefault(); navigate('/jobs'); }}
            >
              Pipeline Jobs
            </BreadcrumbItem>
            <BreadcrumbItem isCurrentPage>Review Invoice</BreadcrumbItem>
          </Breadcrumb>

          {/* Page heading */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
            <Button
              kind="ghost"
              renderIcon={ArrowLeft}
              iconDescription="Back"
              hasIconOnly
              size="sm"
              onClick={() => navigate('/jobs')}
            />
            <div>
              <p className="cds--productive-heading-05" style={{ margin: 0 }}>
                {job?.filename ?? jobId}
              </p>
              <p className="cds--body-long-01" style={{ margin: 0, color: 'var(--cds-text-secondary)' }}>
                Review extracted fields, preview the original file, and approve or reject.
              </p>
            </div>
            {job?.status && (
              <Tag
                type={
                  isReviewable ? 'purple'
                  : job.status.toLowerCase() === 'approved' ? 'green'
                  : job.status.toLowerCase() === 'rejected' ? 'red'
                  : 'gray'
                }
              >
                {job.status}
              </Tag>
            )}
          </div>

          {/* Pipeline progress */}
          <Tile style={{ marginBottom: '1.5rem', padding: '1.25rem' }}>
            <ProgressIndicator currentIndex={currentStep} spaceEqually>
              {PIPELINE_STEPS.map((label, i) => (
                <ProgressStep
                  key={label}
                  label={label}
                  complete={i < currentStep}
                  current={i === currentStep}
                  invalid={
                    (job?.status?.toLowerCase() === 'failed' && i === currentStep) ||
                    (job?.status?.toLowerCase() === 'rejected' && i === currentStep)
                  }
                />
              ))}
            </ProgressIndicator>
          </Tile>

          {/* Summary cards */}
          <Grid fullWidth style={{ marginBottom: '1.5rem', padding: 0 }}>
            <Column lg={4} md={2} sm={4}>
              <Tile style={{ padding: '1rem', height: '100%' }}>
                <p className="cds--label-01" style={{ marginBottom: '0.25rem' }}>Job ID</p>
                <code className="cds--body-short-01" style={{ wordBreak: 'break-all' }}>{job?.job_id}</code>
              </Tile>
            </Column>
            <Column lg={4} md={2} sm={4}>
              <Tile style={{ padding: '1rem', height: '100%' }}>
                <p className="cds--label-01" style={{ marginBottom: '0.25rem' }}>Submitted</p>
                <span className="cds--body-short-02">
                  {job?.submitted_at ? new Date(job.submitted_at).toLocaleString() : '—'}
                </span>
              </Tile>
            </Column>
            <Column lg={4} md={2} sm={4}>
              <Tile style={{ padding: '1rem', height: '100%' }}>
                <p className="cds--label-01" style={{ marginBottom: '0.25rem' }}>Total</p>
                <strong className="cds--productive-heading-03">
                  {job?.staged_header?.INVOICE_AMOUNT != null
                    ? `${job.staged_header.INVOICE_CURRENCY_CODE ?? ''} ${job.staged_header.INVOICE_AMOUNT}`.trim()
                    : '—'}
                </strong>
              </Tile>
            </Column>
            <Column lg={4} md={2} sm={4}>
              <Tile style={{ padding: '1rem', height: '100%' }}>
                <p className="cds--label-01" style={{ marginBottom: '0.25rem' }}>Lines extracted</p>
                <strong className="cds--productive-heading-03">{lineItems.length}</strong>
              </Tile>
            </Column>
          </Grid>

          {/* Error / pipeline failure */}
          {job?.error && (
            <div style={{ marginBottom: '1rem' }}>
              <InlineNotification
                kind="error"
                title="Pipeline error"
                subtitle={job.error}
                lowContrast
              />
            </div>
          )}

          {/* Action notifications */}
          {notice && (
            <div style={{ marginBottom: '1.5rem' }}>
              <InlineNotification
                kind={notice.kind}
                title={notice.title}
                subtitle={notice.subtitle}
                onCloseButtonClick={() => setNotice(null)}
              />
            </div>
          )}

          {/* ---------------------------------------------------------------- */}
          {/* Side-by-side: Header fields (left) + Invoice viewer (right)      */}
          {/* ---------------------------------------------------------------- */}
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start', marginBottom: '2rem' }}>

            {/* Left — extracted header fields */}
            <div style={{ flex: '0 0 50%', minWidth: 0 }}>
              <Layer>
                <TableContainer
                  title="Invoice Header"
                  description={isReviewable ? 'Click any highlighted cell to edit it in place.' : undefined}
                >
                  <div style={{ maxHeight: '75vh', overflowY: 'auto' }}>
                    <Table size="sm">
                      <TableHead>
                        <TableRow>
                          <TableHeader style={{ width: '40%' }}>Field</TableHeader>
                          <TableHeader>Value</TableHeader>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {headerRows.length === 0 ? (
                          <TableRow>
                            <TableCell colSpan={2} style={{ textAlign: 'center', color: 'var(--cds-text-secondary)', padding: '2rem' }}>
                              No header data extracted yet.
                            </TableCell>
                          </TableRow>
                        ) : (
                          headerRows.map(([key, value]) => {
                            const editable = isReviewable && EDITABLE_HEADER_KEYS.has(key);
                            return (
                              <TableRow
                                key={key}
                                style={editable ? { background: 'var(--cds-layer-accent)' } : undefined}
                              >
                                <TableCell>
                                  <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.75rem' }}>
                                    {HEADER_LABEL[key] ?? key}
                                  </span>
                                  {editable && (
                                    <span style={{ marginLeft: '0.4rem', fontSize: '0.65rem', color: 'var(--cds-link-primary)' }}>
                                      editable
                                    </span>
                                  )}
                                </TableCell>
                                <TableCell>
                                  {editable ? (
                                    <EditableCell
                                      value={value}
                                      onSave={(v) => saveHeaderField(key, v)}
                                      disabled={false}
                                    />
                                  ) : (
                                    fmt(value)
                                  )}
                                </TableCell>
                              </TableRow>
                            );
                          })
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </TableContainer>
              </Layer>
            </div>

            {/* Right — invoice file viewer */}
            <div style={{ flex: '0 0 50%', minWidth: 0 }}>
              <Layer>
                <Tile style={{ padding: 0 }}>
                  {/* Collapsible toolbar */}
                  <button
                    onClick={() => setViewerExpanded((v) => !v)}
                    style={{
                      width: '100%', display: 'flex', alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '0.85rem 1rem', background: 'none', border: 'none', cursor: 'pointer',
                      borderBottom: viewerExpanded ? '1px solid var(--cds-border-subtle)' : 'none',
                    }}
                  >
                    <span className="cds--body-short-02">
                      Original Invoice — {job?.filename ?? jobId}
                    </span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                      {/* Zoom controls */}
                      {viewerExpanded && (
                        <div
                          onClick={(e) => e.stopPropagation()}
                          style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}
                        >
                          <button
                            title="Zoom out"
                            disabled={zoom <= ZOOM_MIN}
                            onClick={() => setZoom((z) => Math.max(ZOOM_MIN, z - ZOOM_STEP))}
                            style={{
                              background: 'none', border: '1px solid var(--cds-border-subtle)',
                              borderRadius: 3, padding: '0.2rem 0.35rem',
                              cursor: zoom <= ZOOM_MIN ? 'default' : 'pointer',
                              opacity: zoom <= ZOOM_MIN ? 0.4 : 1, display: 'flex', alignItems: 'center',
                            }}
                          >
                            <ZoomOut size={16} />
                          </button>
                          <span style={{ fontSize: '0.75rem', minWidth: '3rem', textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>
                            {zoom}%
                          </span>
                          <button
                            title="Zoom in"
                            disabled={zoom >= ZOOM_MAX}
                            onClick={() => setZoom((z) => Math.min(ZOOM_MAX, z + ZOOM_STEP))}
                            style={{
                              background: 'none', border: '1px solid var(--cds-border-subtle)',
                              borderRadius: 3, padding: '0.2rem 0.35rem',
                              cursor: zoom >= ZOOM_MAX ? 'default' : 'pointer',
                              opacity: zoom >= ZOOM_MAX ? 0.4 : 1, display: 'flex', alignItems: 'center',
                            }}
                          >
                            <ZoomIn size={16} />
                          </button>
                          {zoom !== 100 && (
                            <button
                              title="Reset zoom"
                              onClick={() => setZoom(100)}
                              style={{
                                background: 'none', border: '1px solid var(--cds-border-subtle)',
                                borderRadius: 3, padding: '0.2rem 0.35rem',
                                cursor: 'pointer', display: 'flex', alignItems: 'center',
                              }}
                            >
                              <Reset size={16} />
                            </button>
                          )}
                        </div>
                      )}
                      {/* Download / open */}
                      <div
                        onClick={(e) => e.stopPropagation()}
                        style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}
                      >
                        <a
                          href={downloadUrl}
                          download={job?.filename ?? 'invoice'}
                          style={{ fontSize: '0.75rem', color: 'var(--cds-link-primary)', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '0.25rem' }}
                        >
                          <Download size={14} />
                          Download
                        </a>
                        {blobUrl && (
                          <button
                            onClick={() => window.open(blobUrl, '_blank')}
                            style={{ background: 'none', border: 'none', padding: 0, fontSize: '0.75rem', color: 'var(--cds-link-primary)', cursor: 'pointer', textDecoration: 'underline' }}
                          >
                            Open in new tab
                          </button>
                        )}
                      </div>
                      {viewerExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </div>
                  </button>

                  {/* Viewer body */}
                  {viewerExpanded && (
                    <div style={{ padding: '0.75rem' }}>
                      {!blobUrl ? (
                        <div style={{
                          height: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
                          border: '1px solid var(--cds-border-subtle)', borderRadius: 2,
                          background: 'var(--cds-layer)', color: 'var(--cds-text-secondary)',
                        }}>
                          <InlineLoading description="Loading file…" />
                        </div>
                      ) : isPdf ? (
                        <iframe
                          src={blobUrl}
                          title="Original Invoice"
                          style={{ width: '100%', height: '70vh', border: '1px solid var(--cds-border-subtle)', borderRadius: 2 }}
                        />
                      ) : (
                        <div style={{
                          overflow: 'auto', height: '70vh',
                          border: '1px solid var(--cds-border-subtle)', borderRadius: 2,
                          background: 'var(--cds-layer)',
                        }}>
                          <img
                            src={blobUrl}
                            alt="Original Invoice"
                            style={{ display: 'block', margin: '0 auto', width: `${zoom}%`, maxWidth: 'none' }}
                          />
                        </div>
                      )}
                    </div>
                  )}
                </Tile>
              </Layer>
            </div>
          </div>

          {/* ---------------------------------------------------------------- */}
          {/* Line items table                                                  */}
          {/* ---------------------------------------------------------------- */}
          <Layer style={{ marginBottom: '2rem' }}>
            <TableContainer
              title={`Invoice Lines (${lineItems.length})`}
              description={isReviewable ? 'Click any cell to edit it in place.' : undefined}
            >
              <Table size="sm">
                <TableHead>
                  <TableRow>
                    {LINE_COLS.map((c) => (
                      <TableHeader key={c.key}>{c.label}</TableHeader>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {lineItems.length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={LINE_COLS.length}
                        style={{ textAlign: 'center', color: 'var(--cds-text-secondary)', padding: '2rem' }}
                      >
                        No lines extracted yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    lineItems.map((line, idx) => (
                      <TableRow key={idx}>
                        {LINE_COLS.map((col) => {
                          const editable = isReviewable;
                          return (
                            <TableCell
                              key={col.key}
                              style={editable ? { background: 'var(--cds-layer-accent)' } : undefined}
                            >
                              {editable ? (
                                <EditableCell
                                  value={line[col.key]}
                                  onSave={(v) => {
                                    const updatedLines = lineItems.map((l, i) =>
                                      i === idx ? { ...l, [col.key]: v } : l
                                    );
                                    updateJob(jobId!, { staged_lines: updatedLines })
                                      .then((saved) => setJob(saved))
                                      .catch((err: unknown) => setNotice({
                                        kind: 'error',
                                        title: 'Line update failed.',
                                        subtitle: (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
                                          ?? (err as Error).message,
                                      }));
                                  }}
                                  disabled={false}
                                />
                              ) : (
                                fmt(line[col.key])
                              )}
                            </TableCell>
                          );
                        })}
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </Layer>

          {/* ---------------------------------------------------------------- */}
          {/* Approve / Reject action bar                                       */}
          {/* ---------------------------------------------------------------- */}
          {isReviewable && (
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
              <Button
                renderIcon={Checkmark}
                disabled={acting}
                onClick={handleApprove}
              >
                {acting ? 'Processing…' : 'Approve Invoice'}
              </Button>
              <Button
                kind="danger"
                renderIcon={Close}
                disabled={acting}
                onClick={() => setRejectOpen(true)}
              >
                Reject Invoice
              </Button>
              {acting && <InlineLoading description="Submitting…" />}
            </div>
          )}

        </Column>
      </Grid>
    </>
  );
};

export default ReviewPage;
