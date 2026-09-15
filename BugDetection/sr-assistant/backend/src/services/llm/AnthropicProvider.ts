import Anthropic from '@anthropic-ai/sdk';
import { LLMProvider, LLMMessage } from './LLMProvider';

export class AnthropicProvider implements LLMProvider {
  private client: Anthropic;
  private model: string;

  constructor(apiKey: string, model: string) {
    this.client = new Anthropic({ apiKey });
    this.model = model;
  }

  async chat(systemPrompt: string, messages: LLMMessage[]): Promise<string> {
    const response = await this.client.messages.create({
      model: this.model,
      max_tokens: 8192,
      system: systemPrompt,
      messages: messages.map((m) => ({ role: m.role, content: m.content })),
    });
    const block = response.content[0];
    if (block.type !== 'text') throw new Error('Unexpected Anthropic response content type');
    return block.text;
  }
}
