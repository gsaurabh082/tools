import * as fs from 'fs';
import * as path from 'path';

const MAX_SNIPPET_BYTES = 4000;
const MAX_TOTAL_BYTES = 40_000;

export interface WorkspaceContext {
  workspacePath: string;
  modules: string[];
  migrationFiles: FileSnippet[];
  entityFiles: FileSnippet[];
  serviceFiles: FileSnippet[];
  summary: string;
}

interface FileSnippet {
  relativePath: string;
  content: string;
}

/**
 * Performs a read-only scan of the DoseWatch reference codebase.
 * Extracts module list, recent Flyway migrations, and files matching SR keywords.
 */
export function scanWorkspace(workspacePath: string, keywords: string[]): WorkspaceContext {
  if (!fs.existsSync(workspacePath)) {
    return empty(workspacePath, `Workspace path not found: ${workspacePath}`);
  }

  const modules = readMavenModules(workspacePath);
  const migrationFiles = findMigrations(workspacePath);
  const entityFiles = findJavaFiles(workspacePath, keywords, ['Entity', 'Repository'], 10);
  const serviceFiles = findJavaFiles(workspacePath, keywords, ['Service', 'Processor', 'Handler', 'Manager'], 8);

  const summary = buildSummary(workspacePath, modules, migrationFiles, entityFiles, serviceFiles);

  return { workspacePath, modules, migrationFiles, entityFiles, serviceFiles, summary };
}

function empty(workspacePath: string, reason: string): WorkspaceContext {
  return {
    workspacePath,
    modules: [],
    migrationFiles: [],
    entityFiles: [],
    serviceFiles: [],
    summary: reason,
  };
}

function readMavenModules(basePath: string): string[] {
  const pomPath = path.join(basePath, 'dosewatch-all', 'pom.xml');
  if (!fs.existsSync(pomPath)) return [];
  try {
    const content = fs.readFileSync(pomPath, 'utf-8');
    const matches = [...content.matchAll(/<module>([^<]+)<\/module>/g)];
    return matches.map((m) => m[1].trim());
  } catch {
    return [];
  }
}

function findMigrations(basePath: string): FileSnippet[] {
  const snippets: FileSnippet[] = [];
  walkDir(basePath, (filePath) => {
    if (snippets.length >= 8) return false;
    if (!/V\d+.*\.sql$/i.test(path.basename(filePath))) return true;
    if (filePath.includes('node_modules') || filePath.includes('target')) return true;
    snippets.push(readSnippet(basePath, filePath));
    return true;
  });
  // Return the last 8 by version number (latest migrations are most relevant)
  return snippets.slice(-8).reverse();
}

function findJavaFiles(
  basePath: string,
  keywords: string[],
  suffixes: string[],
  limit: number,
): FileSnippet[] {
  const lowerKeywords = keywords.map((k) => k.toLowerCase());
  const results: FileSnippet[] = [];
  walkDir(basePath, (filePath) => {
    if (results.length >= limit) return false;
    if (!filePath.endsWith('.java')) return true;
    if (filePath.includes('node_modules') || filePath.includes('target')) return true;
    const base = path.basename(filePath, '.java').toLowerCase();
    const matchesSuffix = suffixes.some((s) => base.endsWith(s.toLowerCase()));
    const matchesKeyword = lowerKeywords.some((kw) => base.includes(kw));
    if (matchesSuffix && matchesKeyword) {
      results.push(readSnippet(basePath, filePath));
    }
    return true;
  });
  return results;
}

function readSnippet(basePath: string, filePath: string): FileSnippet {
  try {
    const raw = fs.readFileSync(filePath, 'utf-8');
    const content = raw.length > MAX_SNIPPET_BYTES
      ? raw.slice(0, MAX_SNIPPET_BYTES) + '\n// ... [truncated]'
      : raw;
    return { relativePath: path.relative(basePath, filePath), content };
  } catch {
    return { relativePath: path.relative(basePath, filePath), content: '// [unreadable]' };
  }
}

/** Depth-first directory walk. Callback returns false to stop. */
function walkDir(dir: string, cb: (filePath: string) => boolean): void {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (['node_modules', '.git', 'target', 'dist', '.mvn'].includes(entry.name)) continue;
      walkDir(full, cb);
    } else if (entry.isFile()) {
      if (!cb(full)) return;
    }
  }
}

function buildSummary(
  workspacePath: string,
  modules: string[],
  migrations: FileSnippet[],
  entities: FileSnippet[],
  services: FileSnippet[],
): string {
  const lines: string[] = [
    `**DoseWatch Workspace:** \`${workspacePath}\``,
    '',
  ];

  if (modules.length) {
    lines.push(`**Maven modules (dosewatch-all):** ${modules.join(', ')}`);
    lines.push('');
  }

  if (migrations.length) {
    lines.push(`**Recent Flyway migrations found (${migrations.length}):**`);
    for (const m of migrations) lines.push(`- \`${m.relativePath}\``);
    lines.push('');
  }

  if (entities.length) {
    lines.push(`**Matching entity/repository files (${entities.length}):**`);
    for (const e of entities) lines.push(`- \`${e.relativePath}\``);
    lines.push('');
  }

  if (services.length) {
    lines.push(`**Matching service/processor files (${services.length}):**`);
    for (const s of services) lines.push(`- \`${s.relativePath}\``);
    lines.push('');
  }

  return lines.join('\n');
}

export function formatWorkspaceForPrompt(ctx: WorkspaceContext): string {
  const parts: string[] = ['## DoseWatch Codebase Context (read-only scan)\n', ctx.summary];

  let totalBytes = Buffer.byteLength(parts.join(''));

  for (const file of [...ctx.entityFiles, ...ctx.serviceFiles]) {
    const block = `\n### ${file.relativePath}\n\`\`\`java\n${file.content}\n\`\`\`\n`;
    if (totalBytes + block.length > MAX_TOTAL_BYTES) break;
    parts.push(block);
    totalBytes += block.length;
  }

  for (const file of ctx.migrationFiles) {
    const block = `\n### ${file.relativePath}\n\`\`\`sql\n${file.content}\n\`\`\`\n`;
    if (totalBytes + block.length > MAX_TOTAL_BYTES) break;
    parts.push(block);
    totalBytes += block.length;
  }

  return parts.join('');
}
