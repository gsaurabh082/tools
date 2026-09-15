import { useState, useRef, FormEvent, ChangeEvent } from 'react';
import axios from 'axios';
import { analyzeSR, buildFormData, fetchJiraIssue } from '../services/api';
import { AnalyzeResponse, JiraIssue, SRInput } from '../types';

interface Props {
  onComplete: (result: AnalyzeResponse) => void;
}

const ENV_OPTIONS: Array<{ value: NonNullable<SRInput['environment']>; label: string }> = [
  { value: 'unknown',        label: 'Unknown' },
  { value: 'production',     label: 'Production' },
  { value: 'staging',        label: 'Staging' },
  { value: 'test',           label: 'Test' },
  { value: 'pre-production', label: 'Pre-Production' },
];

export default function SRInputPanel({ onComplete }: Props) {
  const [sr, setSR] = useState<SRInput>({ environment: 'unknown' });
  const [jiraLoading, setJiraLoading] = useState(false);
  const [jiraIssue, setJiraIssue] = useState<JiraIssue | null>(null);
  const [jiraError, setJiraError] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const prdFileRef  = useRef<HTMLInputElement>(null);
  const logsFileRef = useRef<HTMLInputElement>(null);

  function set(field: keyof SRInput) {
    return (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      setSR((prev) => ({ ...prev, [field]: e.target.value }));
  }

  async function fetchFromJira() {
    if (!sr.jiraKey?.trim()) return;

    // Accept full browse URL: https://jira.company.com/browse/DW-123 → DW-123
    const keyRaw = sr.jiraKey.trim();
    const fromUrl = /\/browse\/([A-Z][A-Z0-9_]+-\d+)/i.exec(keyRaw);
    const key = fromUrl ? fromUrl[1].toUpperCase() : keyRaw.toUpperCase();
    if (fromUrl) setSR((prev) => ({ ...prev, jiraKey: key }));

    setJiraLoading(true);
    setJiraError('');
    try {
      const issue = await fetchJiraIssue(key);
      setJiraIssue(issue);
      setSR((prev) => ({ ...prev, title: issue.summary, description: issue.description }));
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data as Record<string, string>)?.error ?? e.message
        : (e as Error).message;
      setJiraError(`Failed: ${msg}`);
    } finally {
      setJiraLoading(false);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setAnalyzing(true);
    try {
      const fd = buildFormData(sr, prdFileRef.current?.files?.[0], logsFileRef.current?.files?.[0]);
      onComplete(await analyzeSR(fd));
    } catch (err) {
      setError((err as Error).message ?? 'Analysis failed');
    } finally {
      setAnalyzing(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* SR Details */}
      <div className="card p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-4">SR / Defect Details</h2>

        <div className="mb-4">
          <label className="label">Jira Issue Key</label>
          <div className="flex gap-2">
            <input className="input flex-1" placeholder="e.g. DW-12345"
              value={sr.jiraKey ?? ''} onChange={set('jiraKey')} />
            <button type="button" className="btn-secondary whitespace-nowrap"
              onClick={fetchFromJira} disabled={jiraLoading || !sr.jiraKey?.trim()}>
              {jiraLoading ? 'Fetching…' : 'Fetch from Jira'}
            </button>
          </div>
          {jiraError && <p className="text-red-600 text-xs mt-1">{jiraError}</p>}
          {jiraIssue && (
            <div className="text-green-700 text-xs mt-1 space-y-0.5">
              <p className="font-medium">[{jiraIssue.key}] {jiraIssue.summary}</p>
              <p className="text-gray-500">
                {[
                  jiraIssue.priority && `Priority: ${jiraIssue.priority}`,
                  jiraIssue.status && `Status: ${jiraIssue.status}`,
                  jiraIssue.assignee && `Assignee: ${jiraIssue.assignee}`,
                  jiraIssue.comments?.length && `${jiraIssue.comments.length} comments`,
                  jiraIssue.linkedIssues?.length && `${jiraIssue.linkedIssues.length} linked issues`,
                  jiraIssue.attachments?.length && `${jiraIssue.attachments.length} attachments`,
                  Object.keys(jiraIssue.namedCustomFields ?? {}).length &&
                    `${Object.keys(jiraIssue.namedCustomFields).length} custom fields`,
                ].filter(Boolean).join(' · ')}
              </p>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="label">Title / Summary</label>
            <input className="input" placeholder="Brief SR title"
              value={sr.title ?? ''} onChange={set('title')} />
          </div>
          <div>
            <label className="label">Environment</label>
            <select className="input" value={sr.environment ?? 'unknown'} onChange={set('environment')}>
              {ENV_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
        </div>

        <div className="mt-4">
          <label className="label">Description</label>
          <textarea className="textarea min-h-[100px]" placeholder="Full SR description…"
            value={sr.description ?? ''} onChange={set('description')} />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
          <div>
            <label className="label">Customer Impact</label>
            <textarea className="textarea min-h-[80px]" placeholder="How are users impacted?"
              value={sr.customerImpact ?? ''} onChange={set('customerImpact')} />
          </div>
          <div>
            <label className="label">Steps to Reproduce</label>
            <textarea className="textarea min-h-[80px]" placeholder="1. Step one…"
              value={sr.stepsToReproduce ?? ''} onChange={set('stepsToReproduce')} />
          </div>
          <div>
            <label className="label">Expected Behavior</label>
            <textarea className="textarea min-h-[80px]"
              value={sr.expectedBehavior ?? ''} onChange={set('expectedBehavior')} />
          </div>
          <div>
            <label className="label">Actual Behavior</label>
            <textarea className="textarea min-h-[80px]"
              value={sr.actualBehavior ?? ''} onChange={set('actualBehavior')} />
          </div>
        </div>
      </div>

      {/* Evidence */}
      <div className="card p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-4">Evidence &amp; Context</h2>

        <div className="mb-4">
          <label className="label">Logs / Stack Trace</label>
          <textarea className="textarea min-h-[120px] text-xs"
            placeholder="Paste exception or stack trace…"
            value={sr.logsOrStackTrace ?? ''} onChange={set('logsOrStackTrace')} />
          <div className="mt-1">
            <span className="text-xs text-gray-500">or upload: </span>
            <input ref={logsFileRef} type="file" accept=".txt,.log,.xml,.json" className="text-xs" />
          </div>
        </div>

        <div className="mb-4">
          <label className="label">PRD / Design Document</label>
          <textarea className="textarea min-h-[80px]"
            placeholder="Paste PRD text…"
            value={sr.prdDocument ?? ''} onChange={set('prdDocument')} />
          <div className="mt-1">
            <span className="text-xs text-gray-500">or upload: </span>
            <input ref={prdFileRef} type="file" accept=".txt,.md,.pdf,.docx" className="text-xs" />
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="label">Code Snippets</label>
            <textarea className="textarea min-h-[80px] text-xs"
              placeholder="Paste relevant Java / SQL / config…"
              value={sr.codeSnippets ?? ''} onChange={set('codeSnippets')} />
          </div>
          <div>
            <label className="label">DB Schema / SQL Context</label>
            <textarea className="textarea min-h-[80px] text-xs"
              placeholder="Entity structure, Flyway migration, columns…"
              value={sr.dbSchema ?? ''} onChange={set('dbSchema')} />
          </div>
        </div>

        <div className="mt-4">
          <label className="label">Additional Context</label>
          <textarea className="textarea min-h-[60px]"
            placeholder="Other known constraints, notes, related Jira links…"
            value={sr.additionalContext ?? ''} onChange={set('additionalContext')} />
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3 rounded-md">
          {error}
        </div>
      )}

      <div className="flex justify-end">
        <button type="submit" className="btn-primary px-8 py-3 text-base" disabled={analyzing}>
          {analyzing ? (
            <span className="flex items-center gap-2">
              <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Analyzing…
            </span>
          ) : 'Run Analysis'}
        </button>
      </div>
    </form>
  );
}
