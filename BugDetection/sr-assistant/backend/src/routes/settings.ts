import { Router, Request, Response } from 'express';
import { loadSettings, saveSettings } from '../config/settings';
import { AppSettings } from '../types';

const router = Router();

router.get('/', (_req: Request, res: Response) => {
  const settings = loadSettings();
  const safe: AppSettings = JSON.parse(JSON.stringify(settings));
  if (safe.llm.anthropic?.apiKey) safe.llm.anthropic.apiKey = redact(safe.llm.anthropic.apiKey);
  if (safe.jira.apiToken)         safe.jira.apiToken         = redact(safe.jira.apiToken);
  res.json(safe);
});

router.put('/', (req: Request, res: Response) => {
  const incoming = req.body as AppSettings;
  const current = loadSettings();
  // Keep existing secrets if the frontend sent a redacted placeholder
  if (incoming.llm.anthropic?.apiKey?.startsWith('****'))
    incoming.llm.anthropic.apiKey = current.llm.anthropic?.apiKey ?? '';
  if (incoming.jira.apiToken?.startsWith('****'))
    incoming.jira.apiToken = current.jira.apiToken;
  saveSettings(incoming);
  res.json({ ok: true });
});

function redact(value: string): string {
  return value.length <= 4 ? '****' : '****' + value.slice(-4);
}

export default router;
