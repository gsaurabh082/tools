import { Router, Request, Response } from 'express';
import axios from 'axios';
import { loadSettings } from '../config/settings';
import { JiraClient } from '../services/jira/JiraClient';
import { toJiraComment } from '../services/jira/JiraFormatter';
import { AnalyzeResponse } from '../types';

const router = Router();

router.get('/test', async (_req: Request, res: Response) => {
  const { jira } = loadSettings();
  if (!jira.baseUrl) { res.status(400).json({ ok: false, message: 'Jira not configured' }); return; }
  const result = await new JiraClient(jira).testConnection();
  res.json(result);
});

router.get('/:key', async (req: Request, res: Response) => {
  const { jira } = loadSettings();
  const issueUrl = `${jira.baseUrl.replace(/\/$/, '')}/rest/api/2/issue/${req.params.key}`;
  console.log(`[Jira] GET ${issueUrl} (auth: ${jira.authType})`);
  try {
    const issue = await new JiraClient(jira).getIssue(req.params.key);
    res.json(issue);
  } catch (err) {
    const status = axios.isAxiosError(err) ? (err.response?.status ?? 502) : 500;
    const jiraMsg = axios.isAxiosError(err)
      ? (err.response?.data as Record<string, unknown>)?.errorMessages
        ? (err.response!.data as Record<string, string[]>).errorMessages[0]
        : err.message
      : String(err);
    const message = `Jira ${status} — ${jiraMsg}. URL: ${issueUrl}`;
    console.error(`[Jira] ${message}`);
    res.status(status).json({ error: message });
  }
});

router.post('/:key/comment', async (req: Request, res: Response) => {
  const { jira } = loadSettings();
  const body = toJiraComment(req.body as AnalyzeResponse);
  await new JiraClient(jira).addComment(req.params.key, body);
  res.json({ ok: true, message: `Comment posted to ${req.params.key}` });
});

export default router;
