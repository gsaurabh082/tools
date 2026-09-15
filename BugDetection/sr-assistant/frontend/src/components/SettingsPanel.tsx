import { useState, useEffect, FormEvent } from 'react';
import { getSettings, saveSettings, testJiraConnection } from '../services/api';
import { AppSettings } from '../types';

const DEFAULTS: AppSettings = {
  llm: {
    type: 'anthropic',
    anthropic: { apiKey: '', model: 'claude-opus-4-5' },
    ollama:    { baseUrl: 'http://localhost:11434', model: 'llama3.2' },
  },
  jira: { baseUrl: '', username: '', apiToken: '', authType: 'bearer' as const },
  workspacePath: 'C:\\Users\\250020392\\project',
};

export default function SettingsPanel() {
  const [settings, setSettings] = useState<AppSettings>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testResult, setTestResult] = useState('');
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    getSettings().then(setSettings).catch(() => {}).finally(() => setLoading(false));
  }, []);

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaved(false);
    try {
      await saveSettings(settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } finally {
      setSaving(false);
    }
  }

  async function handleTestJira() {
    setTesting(true);
    setTestResult('');
    try {
      const r = await testJiraConnection();
      setTestResult(r.ok
        ? `Connected (Jira ${r.serverInfo ?? ''})`
        : `Failed: ${r.error ?? 'check URL/credentials'}`);
    } catch (e) {
      setTestResult(`Failed: ${(e as Error).message}`);
      setTestResult('Connection failed');
    } finally {
      setTesting(false);
    }
  }

  function patchLLM<K extends keyof AppSettings['llm']>(key: K, value: AppSettings['llm'][K]) {
    setSettings((s) => ({ ...s, llm: { ...s.llm, [key]: value } }));
  }

  if (loading) return <div className="text-center py-12 text-gray-500 text-sm">Loading…</div>;

  return (
    <form onSubmit={handleSave} className="space-y-6 max-w-2xl mx-auto">

      {/* LLM */}
      <div className="card p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-4">LLM Provider</h2>

        <div className="mb-4 flex gap-6">
          {(['anthropic', 'ollama'] as const).map((type) => (
            <label key={type} className="flex items-center gap-2 cursor-pointer">
              <input type="radio" name="llmType" value={type}
                checked={settings.llm.type === type}
                onChange={() => patchLLM('type', type)}
                className="text-brand focus:ring-brand" />
              <span className="text-sm">{type === 'anthropic' ? 'Anthropic (Claude)' : 'Ollama (Local)'}</span>
            </label>
          ))}
        </div>

        {settings.llm.type === 'anthropic' && (
          <div className="space-y-3 border-t border-gray-100 pt-4">
            <div>
              <label className="label">API Key</label>
              <input type="password" className="input" placeholder="sk-ant-…"
                value={settings.llm.anthropic?.apiKey ?? ''}
                onChange={(e) => patchLLM('anthropic', { ...settings.llm.anthropic!, apiKey: e.target.value })} />
            </div>
            <div>
              <label className="label">Model</label>
              <input className="input" placeholder="claude-opus-4-5"
                value={settings.llm.anthropic?.model ?? ''}
                onChange={(e) => patchLLM('anthropic', { ...settings.llm.anthropic!, model: e.target.value })} />
            </div>
          </div>
        )}

        {settings.llm.type === 'ollama' && (
          <div className="space-y-3 border-t border-gray-100 pt-4">
            <div>
              <label className="label">Ollama Base URL</label>
              <input className="input" placeholder="http://localhost:11434"
                value={settings.llm.ollama?.baseUrl ?? ''}
                onChange={(e) => patchLLM('ollama', { ...settings.llm.ollama!, baseUrl: e.target.value })} />
            </div>
            <div>
              <label className="label">Model</label>
              <input className="input" placeholder="llama3.2"
                value={settings.llm.ollama?.model ?? ''}
                onChange={(e) => patchLLM('ollama', { ...settings.llm.ollama!, model: e.target.value })} />
            </div>
          </div>
        )}
      </div>

      {/* Jira */}
      <div className="card p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-4">Jira Server / Data Center</h2>
        <div className="space-y-3">
          <div>
            <label className="label">Auth Type</label>
            <div className="flex gap-6">
              {(['bearer', 'basic'] as const).map((t) => (
                <label key={t} className="flex items-center gap-2 cursor-pointer">
                  <input type="radio" name="jiraAuth" value={t}
                    checked={settings.jira.authType === t}
                    onChange={() => setSettings((s) => ({ ...s, jira: { ...s.jira, authType: t } }))}
                    className="text-brand focus:ring-brand" />
                  <span className="text-sm">{t === 'bearer' ? 'Bearer — Personal Access Token (PAT)' : 'Basic — username + password/token'}</span>
                </label>
              ))}
            </div>
          </div>
          <div>
            <label className="label">Base URL</label>
            <input className="input" placeholder="https://jira.yourcompany.com"
              value={settings.jira.baseUrl}
              onChange={(e) => setSettings((s) => ({ ...s, jira: { ...s.jira, baseUrl: e.target.value } }))} />
          </div>
          {settings.jira.authType === 'basic' && (
          <div>
            <label className="label">Username</label>
            <input className="input" placeholder="your.name@company.com"
              value={settings.jira.username}
              onChange={(e) => setSettings((s) => ({ ...s, jira: { ...s.jira, username: e.target.value } }))} />
          </div>
          )}
          <div>
            <label className="label">{settings.jira.authType === 'bearer' ? 'Personal Access Token' : 'API Token / Password'}</label>
            <input type="password" className="input" placeholder={settings.jira.authType === 'bearer' ? 'Paste PAT here' : 'API token or password'}
              value={settings.jira.apiToken}
              onChange={(e) => setSettings((s) => ({ ...s, jira: { ...s.jira, apiToken: e.target.value } }))} />
          </div>
          <div className="flex items-center gap-3 pt-1">
            <button type="button" className="btn-secondary" onClick={handleTestJira} disabled={testing}>
              {testing ? 'Testing…' : 'Test Connection'}
            </button>
            {testResult && (
              <span className={`text-sm ${testResult.startsWith('Connected') ? 'text-green-700' : 'text-red-600'}`}>
                {testResult}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* DoseWatch workspace path */}
      <div className="card p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-1">DoseWatch Codebase Path</h2>
        <p className="text-xs text-gray-500 mb-3">
          Read-only reference. The backend scans this path for entities, migrations, and services
          matching the SR keywords and includes them as context in the analysis prompt.
        </p>
        <label className="label">Workspace Root</label>
        <input className="input font-mono text-xs" placeholder="C:\Users\...\project"
          value={settings.workspacePath ?? ''}
          onChange={(e) => setSettings((s) => ({ ...s, workspacePath: e.target.value }))} />
      </div>

      <div className="flex justify-end">
        <button type="submit" className="btn-primary px-6" disabled={saving}>
          {saving ? 'Saving…' : saved ? 'Saved!' : 'Save Settings'}
        </button>
      </div>
    </form>
  );
}
