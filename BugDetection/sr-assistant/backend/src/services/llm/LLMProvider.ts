export interface LLMMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface LLMProvider {
  chat(systemPrompt: string, messages: LLMMessage[]): Promise<string>;
}
