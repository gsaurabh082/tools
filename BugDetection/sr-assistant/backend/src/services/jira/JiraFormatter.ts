import { AnalyzeResponse } from '../../types';

/** Converts the analysis report to Jira Server wiki markup for a comment body. */
export function toJiraComment(analysis: AnalyzeResponse): string {
  const { report, analysisId, timestamp } = analysis;
  const lines: string[] = [
    `h2. DoseWatch SR Analysis — ${analysis.jiraKey ?? 'Manual SR'}`,
    `_Generated: ${new Date(timestamp).toLocaleString()} | ID: ${analysisId.slice(0, 8)}_`,
    '',
    ...section('A. Short Summary', report.summary),
    ...section('B. Workspace / Project Inventory', report.projectInventory),
    ...section('C. SR Understanding', report.srUnderstanding),
    ...section('D. PRD Mapping', report.prdMapping),
    ...section('E. Impacted Modules', report.impactedModules),
    ...section('F. Reproduction Plan', report.reproductionPlan),
    ...section('G. Root Cause Analysis', report.rootCauseAnalysis),
    ...section('H. Fix Options', report.fixOptions),
    ...section('I. Recommended Fix', report.recommendedFix),
    ...section('J. Implementation Plan', report.implementationPlan),
    ...section('K. Test Plan', report.testPlan),
    ...section('L. Open Questions / Unverified Items', report.openQuestions),
    ...section('M. Final Engineering Recommendation', report.finalRecommendation),
  ];
  return lines.join('\n');
}

function section(heading: string, content: string): string[] {
  if (!content?.trim()) return [];
  return [`h3. ${heading}`, mdToJira(content), ''];
}

function mdToJira(md: string): string {
  return md
    .replace(/^#### (.+)$/gm, 'h5. $1')
    .replace(/^### (.+)$/gm, 'h4. $1')
    .replace(/^## (.+)$/gm, 'h3. $1')
    .replace(/^# (.+)$/gm, 'h2. $1')
    .replace(/\*\*(.+?)\*\*/g, '*$1*')
    .replace(/__(.+?)__/g, '*$1*')
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '_$1_')
    .replace(/```[\w]*\n([\s\S]*?)```/g, '{code}$1{code}')
    .replace(/`([^`]+)`/g, '{{$1}}')
    .replace(/^- (.+)$/gm, '* $1')
    .replace(/^\d+\. (.+)$/gm, '# $1')
    .replace(/^---+$/gm, '----')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '[$1|$2]');
}
