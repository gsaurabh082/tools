import * as fs from 'fs';
import * as path from 'path';
import { SRInput, JiraIssue } from '../../types';
import { scanWorkspace, formatWorkspaceForPrompt } from './WorkspaceScanner';

const SYSTEM_PROMPT_FILE = path.join(__dirname, '../../../../prompts/sr-analysis-system.md');

export function buildSystemPrompt(): string {
  if (fs.existsSync(SYSTEM_PROMPT_FILE)) {
    return fs.readFileSync(SYSTEM_PROMPT_FILE, 'utf-8');
  }
  throw new Error(`System prompt not found at: ${SYSTEM_PROMPT_FILE}`);
}

export function buildUserMessage(sr: SRInput, jiraIssue?: JiraIssue, workspacePath?: string): string {
  const parts: string[] = ['Please analyze the following Service Request:\n'];

  const title = sr.title ?? jiraIssue?.summary ?? 'Untitled SR';
  const jiraKey = sr.jiraKey ?? jiraIssue?.key;

  // ── Core metadata ────────────────────────────────────────────────
  parts.push('## SR Details');
  if (jiraKey)                          parts.push(`**Jira Key:** ${jiraKey}`);
  parts.push(`**Title:** ${title}`);
  if (jiraIssue?.issueType)             parts.push(`**Issue Type:** ${jiraIssue.issueType}`);
  if (jiraIssue?.priority)              parts.push(`**Priority:** ${jiraIssue.priority}`);
  if (jiraIssue?.status)                parts.push(`**Status:** ${jiraIssue.status}`);
  if (jiraIssue?.resolution)            parts.push(`**Resolution:** ${jiraIssue.resolution}`);
  if (jiraIssue?.resolutionDate)        parts.push(`**Resolved:** ${jiraIssue.resolutionDate}`);
  if (jiraIssue?.reporter)              parts.push(`**Reporter:** ${jiraIssue.reporter}`);
  if (jiraIssue?.assignee)              parts.push(`**Assignee:** ${jiraIssue.assignee}`);
  if (jiraIssue?.created)               parts.push(`**Created:** ${jiraIssue.created}`);
  if (jiraIssue?.updated)               parts.push(`**Updated:** ${jiraIssue.updated}`);
  if (jiraIssue?.components?.length)    parts.push(`**Components:** ${jiraIssue.components.join(', ')}`);
  if (jiraIssue?.labels?.length)        parts.push(`**Labels:** ${jiraIssue.labels.join(', ')}`);
  if (jiraIssue?.fixVersions?.length)   parts.push(`**Fix Versions:** ${jiraIssue.fixVersions.join(', ')}`);
  if (jiraIssue?.affectsVersions?.length) parts.push(`**Affects Versions:** ${jiraIssue.affectsVersions.join(', ')}`);
  if (sr.environment)                   parts.push(`**Environment:** ${sr.environment}`);
  parts.push('');

  // ── Description ──────────────────────────────────────────────────
  const description = sr.description ?? jiraIssue?.description ?? '';
  if (description)          parts.push(`## Description\n${description}\n`);
  if (sr.customerImpact)    parts.push(`## Customer Impact\n${sr.customerImpact}\n`);
  if (sr.stepsToReproduce)  parts.push(`## Steps to Reproduce\n${sr.stepsToReproduce}\n`);
  if (sr.expectedBehavior)  parts.push(`## Expected Behavior\n${sr.expectedBehavior}\n`);
  if (sr.actualBehavior)    parts.push(`## Actual Behavior\n${sr.actualBehavior}\n`);

  // ── Named custom fields (real names from Jira field metadata) ────
  if (jiraIssue?.namedCustomFields && Object.keys(jiraIssue.namedCustomFields).length) {
    parts.push('## Jira Custom Fields');
    for (const [name, value] of Object.entries(jiraIssue.namedCustomFields)) {
      parts.push(`**${name}:**\n${value}\n`);
    }
    parts.push('');
  }

  // ── Linked issues ────────────────────────────────────────────────
  if (jiraIssue?.linkedIssues?.length) {
    parts.push('## Linked Issues');
    for (const li of jiraIssue.linkedIssues) {
      parts.push(`- [${li.key}] (${li.linkType}) ${li.summary} — ${li.issueType}, ${li.status}`);
    }
    parts.push('');
  }

  // ── Sub-tasks ────────────────────────────────────────────────────
  if (jiraIssue?.subTasks?.length) {
    parts.push('## Sub-tasks');
    for (const st of jiraIssue.subTasks) {
      parts.push(`- [${st.key}] ${st.summary} — ${st.status}`);
    }
    parts.push('');
  }

  // ── Attachments ──────────────────────────────────────────────────
  if (jiraIssue?.attachments?.length) {
    parts.push(`## Attachments (${jiraIssue.attachments.length})`);
    for (const a of jiraIssue.attachments) parts.push(`- ${a}`);
    parts.push('');
  }

  // ── Evidence from user ───────────────────────────────────────────
  if (sr.logsOrStackTrace) parts.push(`## Logs / Stack Trace\n\`\`\`\n${sr.logsOrStackTrace}\n\`\`\`\n`);
  if (sr.prdDocument)      parts.push(`## PRD / Design Document\n${sr.prdDocument}\n`);
  if (sr.codeSnippets)     parts.push(`## Code Snippets\n\`\`\`\n${sr.codeSnippets}\n\`\`\`\n`);
  if (sr.dbSchema)         parts.push(`## Database Schema / SQL Context\n${sr.dbSchema}\n`);
  if (sr.additionalContext) parts.push(`## Additional Context\n${sr.additionalContext}\n`);

  // ── Comments (most recent first) ─────────────────────────────────
  if (jiraIssue?.comments?.length) {
    parts.push(`## Jira Comments (${jiraIssue.comments.length}, most recent first)`);
    for (const c of jiraIssue.comments) {
      parts.push(`### ${c.author} — ${c.created}`);
      parts.push(c.body);
      parts.push('');
    }
  }

  // ── Workspace scan ───────────────────────────────────────────────
  if (workspacePath) {
    const keywords = extractKeywords(`${title} ${description} ${sr.customerImpact ?? ''}`);
    if (keywords.length > 0) {
      const ctx = scanWorkspace(workspacePath, keywords);
      if (ctx.modules.length || ctx.entityFiles.length || ctx.serviceFiles.length || ctx.migrationFiles.length) {
        parts.push(formatWorkspaceForPrompt(ctx));
      }
    }
  }

  parts.push('\nPlease respond with a complete structured engineering analysis following the output format (sections A through M).');
  return parts.join('\n');
}

/** Extracts meaningful DoseWatch-domain keywords from free text. */
function extractKeywords(text: string): string[] {
  const stop = new Set([
    'the', 'a', 'an', 'is', 'in', 'of', 'to', 'and', 'or', 'for', 'with',
    'that', 'this', 'when', 'not', 'are', 'be', 'has', 'have', 'was', 'were',
    'on', 'at', 'by', 'from', 'as', 'it', 'its', 'but', 'if', 'so',
  ]);
  return [...new Set(
    text
      .replace(/[^a-zA-Z0-9 _-]/g, ' ')
      .split(/\s+/)
      .map((w) => w.toLowerCase())
      .filter((w) => w.length >= 4 && !stop.has(w)),
  )].slice(0, 20);
}
