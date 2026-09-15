import { Router, Request, Response } from 'express';
import multer from 'multer';
import { v4 as uuidv4 } from 'uuid';
import { loadSettings } from '../config/settings';
import { createLLMProvider } from '../services/llm/LLMFactory';
import { buildSystemPrompt, buildUserMessage } from '../services/analyzer/PromptBuilder';
import { parseReport } from '../services/analyzer/ReportParser';
import { JiraClient } from '../services/jira/JiraClient';
import { AnalyzeResponse, JiraIssue, SRInput } from '../types';

const router = Router();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 5 * 1024 * 1024 } });

router.post('/', upload.fields([
  { name: 'prdFile', maxCount: 1 },
  { name: 'logsFile', maxCount: 1 },
]), async (req: Request, res: Response) => {
  const body = req.body as Record<string, string>;
  const files = req.files as Record<string, Express.Multer.File[]> | undefined;

  const sr: SRInput = {
    jiraKey:           body.jiraKey           || undefined,
    title:             body.title             || undefined,
    description:       body.description       || undefined,
    customerImpact:    body.customerImpact    || undefined,
    stepsToReproduce:  body.stepsToReproduce  || undefined,
    expectedBehavior:  body.expectedBehavior  || undefined,
    actualBehavior:    body.actualBehavior    || undefined,
    logsOrStackTrace:  body.logsOrStackTrace  || undefined,
    prdDocument:       body.prdDocument       || undefined,
    codeSnippets:      body.codeSnippets      || undefined,
    dbSchema:          body.dbSchema          || undefined,
    environment:       (body.environment as SRInput['environment']) || 'unknown',
    additionalContext: body.additionalContext || undefined,
  };

  if (files?.prdFile?.[0])  sr.prdDocument      = files.prdFile[0].buffer.toString('utf-8');
  if (files?.logsFile?.[0]) sr.logsOrStackTrace = files.logsFile[0].buffer.toString('utf-8');

  const settings = loadSettings();

  // Auto-fetch full Jira issue (comments, links, custom fields) when a key is provided
  let jiraIssue: JiraIssue | undefined;
  if (sr.jiraKey && settings.jira.baseUrl) {
    try {
      jiraIssue = await new JiraClient(settings.jira).getIssue(sr.jiraKey);
      console.log(`[Analyze] Fetched Jira ${sr.jiraKey}: ${jiraIssue.comments.length} comments, ${jiraIssue.linkedIssues.length} links, ${Object.keys(jiraIssue.namedCustomFields).length} custom fields`);
    } catch (e) {
      console.warn(`[Analyze] Jira fetch failed for ${sr.jiraKey}:`, (e as Error).message);
    }
  }

  const llm = createLLMProvider(settings.llm);
  const systemPrompt = buildSystemPrompt();
  const userMessage = buildUserMessage(sr, jiraIssue, settings.workspacePath || undefined);

  const rawMarkdown = await llm.chat(systemPrompt, [{ role: 'user', content: userMessage }]);
  const report = parseReport(rawMarkdown);

  const response: AnalyzeResponse = {
    analysisId: uuidv4(),
    timestamp: new Date().toISOString(),
    jiraKey: sr.jiraKey,
    report,
    rawMarkdown,
  };

  res.json(response);
});

export default router;
