import * as fs from 'fs';
import * as path from 'path';
import { AppSettings } from '../types';

const SETTINGS_FILE = path.join(__dirname, '../../data/settings.json');

const DEFAULT_SETTINGS: AppSettings = {
  llm: {
    type: 'anthropic',
    anthropic: {
      apiKey: process.env.ANTHROPIC_API_KEY ?? '',
      model: process.env.ANTHROPIC_MODEL ?? 'claude-opus-4-5',
    },
    ollama: {
      baseUrl: process.env.OLLAMA_BASE_URL ?? 'http://localhost:11434',
      model: process.env.OLLAMA_MODEL ?? 'llama3.2',
    },
  },
  jira: {
    baseUrl: process.env.JIRA_BASE_URL ?? '',
    username: process.env.JIRA_USERNAME ?? '',
    apiToken: process.env.JIRA_API_TOKEN ?? '',
    authType: (process.env.JIRA_AUTH_TYPE ?? 'bearer') as 'bearer' | 'basic',
  },
  workspacePath: process.env.DOSEWATCH_WORKSPACE ?? 'C:\\Users\\250020392\\project',
};

export function loadSettings(): AppSettings {
  try {
    if (fs.existsSync(SETTINGS_FILE)) {
      const raw = fs.readFileSync(SETTINGS_FILE, 'utf-8');
      return JSON.parse(raw) as AppSettings;
    }
  } catch {
    // fall through to defaults
  }
  return DEFAULT_SETTINGS;
}

export function saveSettings(settings: AppSettings): void {
  const dir = path.dirname(SETTINGS_FILE);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2), 'utf-8');
}
