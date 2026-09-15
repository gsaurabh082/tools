import { AnalysisReport } from '../../types';

const SECTION_PATTERNS: Array<[keyof AnalysisReport, RegExp]> = [
  ['summary',            /###\s+A\.\s+Short Summary\s*\n([\s\S]*?)(?=###\s+[B-M]\.|\s*$)/i],
  ['projectInventory',   /###\s+B\.\s+Workspace\s*\/\s*Project Inventory\s*\n([\s\S]*?)(?=###\s+[C-M]\.|\s*$)/i],
  ['srUnderstanding',    /###\s+C\.\s+SR Understanding\s*\n([\s\S]*?)(?=###\s+[D-M]\.|\s*$)/i],
  ['prdMapping',         /###\s+D\.\s+PRD Mapping\s*\n([\s\S]*?)(?=###\s+[E-M]\.|\s*$)/i],
  ['impactedModules',    /###\s+E\.\s+Impacted Modules\s*\n([\s\S]*?)(?=###\s+[F-M]\.|\s*$)/i],
  ['reproductionPlan',   /###\s+F\.\s+Reproduction Plan\s*\n([\s\S]*?)(?=###\s+[G-M]\.|\s*$)/i],
  ['rootCauseAnalysis',  /###\s+G\.\s+Root Cause Analysis\s*\n([\s\S]*?)(?=###\s+[H-M]\.|\s*$)/i],
  ['fixOptions',         /###\s+H\.\s+Fix Options\s*\n([\s\S]*?)(?=###\s+[I-M]\.|\s*$)/i],
  ['recommendedFix',     /###\s+I\.\s+Recommended Fix\s*\n([\s\S]*?)(?=###\s+[J-M]\.|\s*$)/i],
  ['implementationPlan', /###\s+J\.\s+Implementation Plan\s*\n([\s\S]*?)(?=###\s+[K-M]\.|\s*$)/i],
  ['testPlan',           /###\s+K\.\s+Test Plan\s*\n([\s\S]*?)(?=###\s+[L-M]\.|\s*$)/i],
  ['openQuestions',      /###\s+L\.\s+Open Questions[\s\S]*?\n([\s\S]*?)(?=###\s+M\.|\s*$)/i],
  ['finalRecommendation',/###\s+M\.\s+Final Engineering Recommendation\s*\n([\s\S]*?)$/i],
];

export function parseReport(rawMarkdown: string): AnalysisReport {
  const report: AnalysisReport = {
    summary: '', projectInventory: '', srUnderstanding: '', prdMapping: '',
    impactedModules: '', reproductionPlan: '', rootCauseAnalysis: '', fixOptions: '',
    recommendedFix: '', implementationPlan: '', testPlan: '', openQuestions: '',
    finalRecommendation: '',
  };

  for (const [key, pattern] of SECTION_PATTERNS) {
    const match = pattern.exec(rawMarkdown);
    if (match?.[1]) report[key] = match[1].trim();
  }

  // Fallback: if no sections were parsed, put everything in summary
  if (!Object.values(report).some((v) => v.length > 0)) {
    report.summary = rawMarkdown.trim();
  }

  return report;
}
