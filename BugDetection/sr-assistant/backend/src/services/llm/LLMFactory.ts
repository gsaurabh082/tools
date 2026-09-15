import { LLMProviderConfig } from '../../types';
import { LLMProvider } from './LLMProvider';
import { AnthropicProvider } from './AnthropicProvider';
import { OllamaProvider } from './OllamaProvider';

export function createLLMProvider(config: LLMProviderConfig): LLMProvider {
  if (config.type === 'ollama') {
    const { baseUrl, model } = config.ollama!;
    if (!baseUrl || !model) throw new Error('Ollama baseUrl and model are required');
    return new OllamaProvider(baseUrl, model);
  }
  const { apiKey, model } = config.anthropic!;
  if (!apiKey) throw new Error('Anthropic API key is required');
  return new AnthropicProvider(apiKey, model);
}
