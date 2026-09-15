import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { postJiraComment } from '../services/api';
import { AnalysisReport as Report, AnalyzeResponse } from '../types';

interface Props {
  analysis: AnalyzeResponse;
  onNewAnalysis: () => void;
}

const SECTIONS: Array<{ id: keyof Report; label: string }> = [
  { id: 'summary',            label: 'A. Short Summary' },
  { id: 'projectInventory',   label: 'B. Project Inventory' },
  { id: 'srUnderstanding',    label: 'C. SR Understanding' },
  { id: 'prdMapping',         label: 'D. PRD Mapping' },
  { id: 'impactedModules',    label: 'E. Impacted Modules' },
  { id: 'reproductionPlan',   label: 'F. Reproduction Plan' },
  { id: 'rootCauseAnalysis',  label: 'G. Root Cause Analysis' },
  { id: 'fixOptions',         label: 'H. Fix Options' },
  { id: 'recommendedFix',     label: 'I. Recommended Fix' },
  { id: 'implementationPlan', label: 'J. Implementation Plan' },
  { id: 'testPlan',           label: 'K. Test Plan' },
  { id: 'openQuestions',      label: 'L. Open Questions' },
  { id: 'finalRecommendation',label: 'M. Final Recommendation' },
];

// Default: summary, recommended fix, and final recommendation open
const DEFAULT_OPEN = new Set<keyof Report>(['summary', 'recommendedFix', 'finalRecommendation']);

export default function AnalysisReport({ analysis, onNewAnalysis }: Props) {
  const [expanded, setExpanded] = useState<Set<keyof Report>>(DEFAULT_OPEN);
  const [view, setView] = useState<'sections' | 'raw'>('sections');
  const [posting, setPosting] = useState(false);
  const [postResult, setPostResult] = useState('');

  function toggle(id: keyof Report) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function handlePostToJira() {
    if (!analysis.jiraKey) return;
    setPosting(true);
    setPostResult('');
    try {
      await postJiraComment(analysis.jiraKey, analysis);
      setPostResult(`Comment posted to ${analysis.jiraKey}`);
    } catch (e) {
      setPostResult(`Failed: ${(e as Error).message}`);
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="card p-4 flex flex-wrap items-center gap-3 justify-between">
        <div>
          <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2">
            Analysis Report
            {analysis.jiraKey && (
              <span className="badge bg-blue-100 text-blue-800">{analysis.jiraKey}</span>
            )}
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {new Date(analysis.timestamp).toLocaleString()} · ID: {analysis.analysisId.slice(0, 8)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={onNewAnalysis}>New Analysis</button>
          <button className="btn-ghost" onClick={() => setView(view === 'sections' ? 'raw' : 'sections')}>
            {view === 'sections' ? 'Raw Markdown' : 'Section View'}
          </button>
          <button className="btn-ghost" onClick={() => navigator.clipboard.writeText(analysis.rawMarkdown)}>
            Copy Markdown
          </button>
          {analysis.jiraKey && (
            <button className="btn-primary" onClick={handlePostToJira} disabled={posting}>
              {posting ? 'Posting…' : `Post to ${analysis.jiraKey}`}
            </button>
          )}
        </div>
      </div>

      {postResult && (
        <div className={`px-4 py-2 rounded text-sm border ${
          postResult.startsWith('Failed')
            ? 'bg-red-50 text-red-700 border-red-200'
            : 'bg-green-50 text-green-700 border-green-200'
        }`}>
          {postResult}
        </div>
      )}

      {view === 'raw' ? (
        <div className="card p-6">
          <pre className="text-xs font-mono whitespace-pre-wrap text-gray-700 overflow-auto max-h-[70vh]">
            {analysis.rawMarkdown}
          </pre>
        </div>
      ) : (
        <>
          <div className="flex gap-3 text-xs">
            <button className="text-brand hover:underline"
              onClick={() => setExpanded(new Set(SECTIONS.map((s) => s.id)))}>Expand all</button>
            <span className="text-gray-300">|</span>
            <button className="text-brand hover:underline"
              onClick={() => setExpanded(new Set())}>Collapse all</button>
          </div>

          <div className="space-y-2">
            {SECTIONS.map(({ id, label }) => {
              const content = analysis.report[id];
              if (!content) return null;
              const open = expanded.has(id);
              return (
                <div key={id} className="card overflow-hidden">
                  <button
                    className="w-full flex items-center justify-between px-5 py-3 text-left hover:bg-gray-50 transition-colors"
                    onClick={() => toggle(id)}
                  >
                    <span className="text-sm font-semibold text-gray-800">{label}</span>
                    <svg className={`w-4 h-4 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`}
                      fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                  {open && (
                    <div className="px-5 pb-5 border-t border-gray-100">
                      <div className="prose-report mt-3">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
