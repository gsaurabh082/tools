/// <reference types="vite/client" />
import type { WinColimaBridge } from "../shared/types";
declare global { interface Window { wincolima: WinColimaBridge & { openCliGuide(): Promise<void> } } }
export {};
