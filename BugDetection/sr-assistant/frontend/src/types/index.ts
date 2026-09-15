export interface SRInput {
  jiraKey?: string;
  title?: string;
  description?: string;
  customerImpact?: string;
  stepsToReproduce?: string;
  expectedBehavior?: string;
  actualBehavior?: string;
  logsOrStackTrace?: string;
  prdDocument?: string;
  codeSnippets?: string;
  dbSchema?: string;
  environment?: 'production' | 'staging' | 'test' | 'pre-production' | 'unknown';
  additionalContext?: string;
}

export interface AnalysisReport {
  summary: string;
  projectInventory: string;
  srUnderstanding: string;
  prdMapping: string;
  impactedModules: string;
  reproductionPlan: string;
  rootCauseAnalysis: string;
  fixOptions: string;
  recommendedFix: string;
  implementationPlan: string;
  testPlan: string;
  openQuestions: string;
  finalRecommendation: string;
}

export interface AnalyzeResponse {
  analysisId: string;
  timestamp: string;
  jiraKey?: string;
  report: AnalysisReport;
  rawMarkdown: string;
}

export interface JiraIssue {
  key: string;
  summary: string;
  description: string;
  issueType: string;
  priority: string;
  status: string;
  resolution?: string;
  resolutionDate?: string;
  reporter: string;
  assignee?: string;
  created: string;
  updated: string;
  labels: string[];
  components: string[];
  fixVersions: string[];
  affectsVersions: string[];
  subTasks: { key: string; summary: string; status: string }[];
  attachments: string[];
  linkedIssues: { linkType: string; key: string; summary: string; status: string; issueType: string }[];
  comments: { id: string; author: string; created: string; body: string }[];
  namedCustomFields: Record<string, string>;
}

export interface AppSettings {
  llm: {
    type: 'anthropic' | 'ollama';
    anthropic?: { apiKey: string; model: string };
    ollama?: { baseUrl: string; model: string };
  };
  jira: {
    baseUrl: string;
    username: string;
    apiToken: string;
    authType: 'bearer' | 'basic';
  };
  workspacePath: string;
}
