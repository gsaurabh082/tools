import axios, { AxiosInstance } from 'axios';
import * as https from 'https';
import { JiraConfig, JiraIssue, JiraComment, JiraLinkedIssue } from '../../types';

// Custom fields whose values are too large or machine-generated to be useful in analysis
const BLOCKLIST_FIELDS = new Set(['customfield_14500', 'customfield_11300']);
const MAX_COMMENT_BODY = 3000;
const MAX_COMMENTS = 30;
const MAX_CUSTOM_FIELD_VALUE = 2000;

export class JiraClient {
  private http: AxiosInstance;

  constructor(config: JiraConfig) {
    const authHeader = config.authType === 'bearer'
      ? `Bearer ${config.apiToken}`
      : `Basic ${Buffer.from(`${config.username}:${config.apiToken}`).toString('base64')}`;

    this.http = axios.create({
      baseURL: config.baseUrl.replace(/\/$/, ''),
      headers: {
        Authorization: authHeader,
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      timeout: 30_000,
      // Allow self-signed / internal GE certs
      httpsAgent: new https.Agent({ rejectUnauthorized: false }),
    });
  }

  async getIssue(issueKey: string): Promise<JiraIssue> {
    // expand=names gives us field-ID → display-name mapping; fields=*all pulls every field
    const { data } = await this.http.get(
      `/rest/api/2/issue/${issueKey}?expand=names,renderedFields&fields=*all`,
    );

    const fields         = data.fields         as Record<string, unknown>;
    const renderedFields  = (data.renderedFields as Record<string, unknown>) ?? {};
    const names           = (data.names          as Record<string, string>)  ?? {};

    // --- helpers ---
    const str = (v: unknown): string => {
      if (!v) return '';
      if (typeof v === 'string') return v;
      if (typeof v === 'object' && 'content' in (v as object)) return docText(v as DocNode);
      if (typeof v === 'object' && 'value' in (v as object))
        return String((v as Record<string, unknown>).value);
      return '';
    };

    const nameList = (arr: unknown): string[] =>
      Array.isArray(arr) ? (arr as Array<Record<string, string>>).map((x) => x.name ?? x.key ?? '') : [];

    // --- core fields ---
    const issue: JiraIssue = {
      key:            data.key as string,
      summary:        str(fields.summary),
      description:    str(fields.description),
      issueType:      str((fields.issuetype  as Record<string, unknown>)?.name),
      priority:       str((fields.priority   as Record<string, unknown>)?.name),
      status:         str((fields.status     as Record<string, unknown>)?.name),
      resolution:     str((fields.resolution as Record<string, unknown>)?.name) || undefined,
      // resolutiondate is the ISO timestamp; resolution is the name (e.g. "Fixed")
      resolutionDate: str(fields.resolutiondate) || undefined,
      reporter:       str((fields.reporter   as Record<string, unknown>)?.displayName),
      assignee:       str((fields.assignee   as Record<string, unknown>)?.displayName) || undefined,
      created:        str(fields.created),
      updated:        str(fields.updated),
      labels:         (fields.labels as string[]) ?? [],
      components:     nameList(fields.components),
      fixVersions:    nameList(fields.fixVersions),
      affectsVersions: nameList(fields.versions),

      // --- sub-tasks ---
      subTasks: ((fields.subtasks as Array<Record<string, unknown>>) ?? []).map((st) => ({
        key:     st.key as string,
        summary: str((st.fields as Record<string, unknown>)?.summary),
        status:  str(((st.fields as Record<string, unknown>)?.status as Record<string, unknown>)?.name),
      })),

      // --- attachments (names only — binary content not fetched) ---
      attachments: ((fields.attachment as Array<Record<string, string>>) ?? [])
        .map((a) => `${a.filename} (${a.mimeType ?? 'unknown'}, ${formatBytes(Number(a.size ?? 0))})`),

      // --- linked issues ---
      linkedIssues: extractLinkedIssues(fields.issuelinks),

      // --- comments (most recent first, limited) ---
      comments: extractComments(fields.comment),

      // --- named custom fields ---
      namedCustomFields: extractNamedCustomFields(fields, renderedFields, names),
    };

    return issue;
  }

  async addComment(issueKey: string, body: string): Promise<void> {
    await this.http.post(`/rest/api/2/issue/${issueKey}/comment`, { body });
  }

  async testConnection(): Promise<{ ok: boolean; serverInfo?: string; error?: string }> {
    try {
      const { data } = await this.http.get('/rest/api/2/serverInfo');
      return { ok: true, serverInfo: (data as Record<string, string>).version };
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? `HTTP ${err.response?.status ?? 'no-response'}: ${err.message}`
        : String(err);
      return { ok: false, error: msg };
    }
  }
}

// ── helpers ──────────────────────────────────────────────────────────────────

interface DocNode { type?: string; text?: string; content?: DocNode[] }

function docText(node: DocNode): string {
  if (node.text) return node.text;
  if (node.content) return node.content.map(docText).join('');
  return '';
}

function extractLinkedIssues(raw: unknown): JiraLinkedIssue[] {
  if (!Array.isArray(raw)) return [];
  const results: JiraLinkedIssue[] = [];
  for (const link of raw as Array<Record<string, unknown>>) {
    const type = (link.type as Record<string, string>)?.outward
      ?? (link.type as Record<string, string>)?.inward
      ?? 'relates to';

    const target = (link.outwardIssue ?? link.inwardIssue) as Record<string, unknown> | undefined;
    if (!target) continue;
    const f = target.fields as Record<string, unknown> | undefined;
    results.push({
      linkType:  type,
      key:       target.key as string,
      summary:   String(f?.summary ?? ''),
      status:    String((f?.status as Record<string, unknown>)?.name ?? ''),
      issueType: String((f?.issuetype as Record<string, unknown>)?.name ?? ''),
    });
  }
  return results;
}

function extractComments(raw: unknown): JiraComment[] {
  const commentBlock = raw as Record<string, unknown> | undefined;
  if (!commentBlock?.comments) return [];
  const all = commentBlock.comments as Array<Record<string, unknown>>;
  // Take most recent MAX_COMMENTS, newest first
  return all
    .slice(-MAX_COMMENTS)
    .reverse()
    .map((c) => ({
      id:      String(c.id ?? ''),
      author:  String((c.author as Record<string, unknown>)?.displayName ?? 'Unknown'),
      created: String(c.created ?? ''),
      body:    truncate(
        typeof c.body === 'string' ? c.body : docText(c.body as DocNode),
        MAX_COMMENT_BODY,
      ),
    }));
}

function extractNamedCustomFields(
  fields: Record<string, unknown>,
  renderedFields: Record<string, unknown>,
  names: Record<string, string>,
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(fields)) {
    if (!key.startsWith('customfield_')) continue;
    if (BLOCKLIST_FIELDS.has(key)) continue;
    if (!value) continue;

    const displayName = names[key] ?? key;
    let strVal = '';

    if (typeof value === 'string') {
      // Prefer rendered HTML stripped to plain text over raw wiki markup
      const rendered = renderedFields[key];
      strVal = rendered && typeof rendered === 'string' ? stripHtml(rendered) : cleanWikiMarkup(value);
    } else if (typeof value === 'number') {
      strVal = String(value);
    } else if (typeof value === 'object') {
      if ('value' in (value as object)) strVal = String((value as Record<string, unknown>).value);
      else if ('displayName' in (value as object)) strVal = String((value as Record<string, unknown>).displayName);
      else if ('name' in (value as object)) strVal = String((value as Record<string, unknown>).name);
      else if ('content' in (value as object)) strVal = docText(value as DocNode);
      else if (Array.isArray(value)) {
        strVal = (value as Array<Record<string, unknown>>)
          .map((v) => v.value ?? v.name ?? v.displayName ?? String(v))
          .join(', ');
      }
    }

    if (!strVal || strVal === 'null') continue;
    // Skip machine-generated junk (Java object toString output)
    if (strVal.includes('com.atlassian') || strVal.startsWith('0|')) continue;

    result[displayName] = truncate(strVal, MAX_CUSTOM_FIELD_VALUE);
  }
  return result;
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + '\n… [truncated]' : s;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Strip HTML tags, decode common entities, collapse whitespace. */
function stripHtml(html: string): string {
  return html
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<\/li>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"').replace(/&#160;/g, ' ').replace(/&nbsp;/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** Clean up common Jira wiki markup tokens to readable plain text. */
function cleanWikiMarkup(s: string): string {
  return s
    .replace(/\{code(?::[^}]*)\}/gi, '```')   // {code:java} → ```
    .replace(/\{code\}/gi, '```')
    .replace(/\{noformat\}/gi, '')
    .replace(/\+([^+]+)\+/g, '$1')            // +underline+ → text
    .replace(/\*([^*]+)\*/g, '$1')            // *bold* → text
    .replace(/_([^_]+)_/g, '$1')              // _italic_ → text
    .replace(/\[\^[^\]]+\]/g, '[attachment]') // [^file.xlsx] → [attachment]
    .replace(/\[([^|\]]+)\|[^\]]+\]/g, '$1') // [text|url] → text
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

