import axios from 'axios';
import { AnalyzeResponse, AppSettings, JiraIssue, SRInput } from '../types';

const api = axios.create({ baseURL: '/api' });

export async function analyzeSR(formData: FormData): Promise<AnalyzeResponse> {
  const { data } = await api.post<AnalyzeResponse>('/analyze', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300_000,
  });
  return data;
}

export async function fetchJiraIssue(key: string): Promise<JiraIssue> {
  const { data } = await api.get<JiraIssue>(`/jira/${key.trim().toUpperCase()}`);
  return data;
}

export async function postJiraComment(jiraKey: string, analysis: AnalyzeResponse): Promise<void> {
  await api.post(`/jira/${jiraKey}/comment`, analysis);
}

export async function getSettings(): Promise<AppSettings> {
  const { data } = await api.get<AppSettings>('/settings');
  return data;
}

export async function saveSettings(settings: AppSettings): Promise<void> {
  await api.put('/settings', settings);
}

export async function testJiraConnection(): Promise<{ ok: boolean; serverInfo?: string; error?: string }> {
  const { data } = await api.get('/jira/test');
  return data;
}

export function buildFormData(sr: SRInput, prdFile?: File, logsFile?: File): FormData {
  const fd = new FormData();
  const fields: Array<keyof SRInput> = [
    'jiraKey', 'title', 'description', 'customerImpact', 'stepsToReproduce',
    'expectedBehavior', 'actualBehavior', 'logsOrStackTrace', 'prdDocument',
    'codeSnippets', 'dbSchema', 'environment', 'additionalContext',
  ];
  for (const field of fields) {
    const val = sr[field];
    if (val) fd.append(field, val);
  }
  if (prdFile)  fd.append('prdFile', prdFile);
  if (logsFile) fd.append('logsFile', logsFile);
  return fd;
}
