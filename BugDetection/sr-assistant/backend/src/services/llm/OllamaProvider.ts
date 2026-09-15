import axios from 'axios';
import { LLMProvider, LLMMessage } from './LLMProvider';

interface OllamaChatResponse {
  message: { role: string; content: string };
  done: boolean;
}

export class OllamaProvider implements LLMProvider {
  private baseUrl: string;
  private model: string;

  constructor(baseUrl: string, model: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.model = model;
  }

  async chat(systemPrompt: string, messages: LLMMessage[]): Promise<string> {
    const allMessages = [
      { role: 'system', content: systemPrompt },
      ...messages.map((m) => ({ role: m.role, content: m.content })),
    ];
    const response = await axios.post<OllamaChatResponse>(
      `${this.baseUrl}/api/chat`,
      { model: this.model, messages: allMessages, stream: false, options: { num_predict: 8192 } },
      { timeout: 300_000 },
    );
    return response.data.message.content;
  }
}
