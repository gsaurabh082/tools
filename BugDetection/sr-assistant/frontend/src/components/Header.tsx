type View = 'input' | 'report' | 'settings';

interface Props {
  view: View;
  onNavigate: (view: View) => void;
  hasReport: boolean;
}

export default function Header({ view, onNavigate, hasReport }: Props) {
  return (
    <header className="bg-brand text-white shadow-md">
      <div className="container mx-auto px-4 max-w-6xl flex items-center justify-between h-14">
        <div className="flex items-center gap-3">
          <svg className="w-5 h-5 opacity-90" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span className="font-semibold text-sm tracking-wide">DoseWatch SR Assistant</span>
        </div>
        <nav className="flex items-center gap-1">
          {(['input', ...(hasReport ? ['report'] : []), 'settings'] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => onNavigate(v)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                view === v ? 'bg-white text-brand' : 'text-white hover:bg-brand-dark'
              }`}
            >
              {v === 'input' ? 'New Analysis' : v === 'report' ? 'Report' : 'Settings'}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
}
