import { useState } from 'react';
import Header from './components/Header';
import SRInputPanel from './components/SRInputPanel';
import AnalysisReport from './components/AnalysisReport';
import SettingsPanel from './components/SettingsPanel';
import { AnalyzeResponse } from './types';

type View = 'input' | 'report' | 'settings';

export default function App() {
  const [view, setView] = useState<View>('input');
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);

  function handleAnalysisComplete(result: AnalyzeResponse) {
    setAnalysis(result);
    setView('report');
  }

  return (
    <div className="min-h-screen flex flex-col">
      <Header view={view} onNavigate={(v) => setView(v as View)} hasReport={analysis !== null} />

      <main className="flex-1 container mx-auto px-4 py-6 max-w-6xl">
        {view === 'input'    && <SRInputPanel onComplete={handleAnalysisComplete} />}
        {view === 'report'   && analysis && (
          <AnalysisReport analysis={analysis} onNewAnalysis={() => setView('input')} />
        )}
        {view === 'settings' && <SettingsPanel />}
      </main>

      <footer className="text-center text-xs text-gray-400 py-4 border-t border-gray-100">
        DoseWatch SR-to-Fix Engineering Assistant
      </footer>
    </div>
  );
}
