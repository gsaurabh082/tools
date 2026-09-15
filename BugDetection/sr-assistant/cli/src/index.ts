#!/usr/bin/env node
import * as fs from 'fs';
import * as path from 'path';
import { Command } from 'commander';
import axios from 'axios';

const program = new Command();

program
  .name('sr-analyze')
  .description('DoseWatch SR-to-Fix Engineering Assistant CLI')
  .version('1.0.0');

program
  .command('analyze')
  .description('Analyze an SR and print the report')
  .requiredOption('--backend <url>', 'Backend API base URL', 'http://localhost:3001')
  .option('--jira-key <key>',    'Jira issue key to fetch and analyze')
  .option('--file <path>',       'JSON or Markdown file containing SR details')
  .option('--title <title>',     'SR title')
  .option('--description <text>','SR description')
  .option('--env <env>',         'Environment (production|staging|test|pre-production|unknown)', 'unknown')
  .option('--logs <path>',       'Log / stack-trace file to attach')
  .option('--prd <path>',        'PRD document file to attach')
  .option('--output <path>',     'Save the Markdown report to this file instead of stdout')
  .option('--post-jira',         'Post the analysis as a Jira comment (requires --jira-key)')
  .action(async (opts: {
    backend: string; jiraKey?: string; file?: string; title?: string;
    description?: string; env: string; logs?: string; prd?: string;
    output?: string; postJira?: boolean;
  }) => {
    const baseUrl = opts.backend.replace(/\/$/, '');

    // Node 18+ has native FormData in globalThis, but for older Node we fall back to axios form
    const fd = new (globalThis.FormData ?? require('form-data'))() as FormData;

    const append = (key: string, value: string) => fd.append(key, value);

    if (opts.jiraKey) append('jiraKey', opts.jiraKey);
    append('environment', opts.env);

    if (opts.file) {
      const raw = fs.readFileSync(path.resolve(opts.file), 'utf-8');
      if (opts.file.endsWith('.json')) {
        const sr = JSON.parse(raw) as Record<string, string>;
        for (const [k, v] of Object.entries(sr)) append(k, v);
      } else {
        append('description', raw);
      }
    }

    if (opts.title)       append('title', opts.title);
    if (opts.description) append('description', opts.description);

    if (opts.logs) {
      const content = fs.readFileSync(path.resolve(opts.logs), 'utf-8');
      const blob = new Blob([content], { type: 'text/plain' });
      fd.append('logsFile', blob, path.basename(opts.logs));
    }

    if (opts.prd) {
      const content = fs.readFileSync(path.resolve(opts.prd), 'utf-8');
      const blob = new Blob([content], { type: 'text/plain' });
      fd.append('prdFile', blob, path.basename(opts.prd));
    }

    console.error(`Sending to ${baseUrl}/api/analyze …`);

    const { data } = await axios.post(`${baseUrl}/api/analyze`, fd, {
      timeout: 300_000,
      maxBodyLength: Infinity,
    });

    const analysis = data as { rawMarkdown: string; jiraKey?: string };

    if (opts.output) {
      fs.writeFileSync(path.resolve(opts.output), analysis.rawMarkdown, 'utf-8');
      console.error(`Report saved to ${opts.output}`);
    } else {
      process.stdout.write(analysis.rawMarkdown + '\n');
    }

    if (opts.postJira && opts.jiraKey) {
      console.error(`Posting comment to Jira ${opts.jiraKey} …`);
      await axios.post(`${baseUrl}/api/jira/${opts.jiraKey}/comment`, data);
      console.error('Comment posted.');
    }
  });

program
  .command('settings')
  .description('Show current settings (secrets redacted)')
  .option('--backend <url>', 'Backend API base URL', 'http://localhost:3001')
  .action(async (opts: { backend: string }) => {
    const { data } = await axios.get(`${opts.backend}/api/settings`);
    console.log(JSON.stringify(data, null, 2));
  });

program
  .command('test-jira')
  .description('Test Jira connectivity via the backend')
  .option('--backend <url>', 'Backend API base URL', 'http://localhost:3001')
  .action(async (opts: { backend: string }) => {
    const { data } = await axios.get(`${opts.backend}/api/jira/test`);
    const r = data as { ok: boolean; serverInfo?: string };
    console.log(r.ok ? `OK — Jira ${r.serverInfo ?? ''}` : 'FAILED');
  });

program.parseAsync(process.argv).catch((err: Error) => {
  console.error('Error:', err.message);
  process.exit(1);
});
