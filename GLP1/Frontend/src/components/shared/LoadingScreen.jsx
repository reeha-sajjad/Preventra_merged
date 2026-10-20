
export default function LoadingScreen({ progress = 0, status = 'Loading…' }) {
  return (
    <div
      className="fixed inset-0 flex items-center justify-center z-50"
      style={{
        background: 'linear-gradient(135deg, #0F1B2D 0%, #1B4F8A 100%)',
      }}
    >
      <div className="flex flex-col items-center gap-6 max-w-sm w-full px-8">
        {/* Logo */}
        <div className="relative">
          <div
            className="w-16 h-16 rounded-2xl flex items-center justify-center shadow-2xl animate-pulse"
            style={{ background: 'rgba(255,255,255,0.08)', backdropFilter: 'blur(8px)' }}
          >
            <img src="/preventra-logo.png" alt="Preventra" className="h-14 w-auto object-contain" />
          </div>
          <div
            className="absolute inset-0 rounded-2xl"
            style={{
              boxShadow: '0 0 60px 8px rgba(255,255,255,0.15)',
              animation: 'pulse 2s ease-in-out infinite',
            }}
          />
        </div>

        {/* Title */}
        <div className="text-center">
          <h1
            className="text-white text-2xl font-semibold tracking-tight"
            style={{ fontFamily: 'var(--p-font-display)' }}
          >
            GLP-1 Analytics
          </h1>
          <p className="text-white/50 text-xs tracking-widest uppercase mt-1">
            Adherence & Cost Intelligence
          </p>
        </div>

        {/* Progress bar */}
        <div className="w-full mt-2">
          <div
            className="h-1 rounded-full overflow-hidden"
            style={{ background: 'rgba(255,255,255,0.1)' }}
          >
            <div
              className="h-full rounded-full transition-all duration-500 ease-out"
              style={{
                width: `${Math.max(5, progress)}%`,
                background: 'linear-gradient(90deg, #60A5FA, #93C5FD)',
                boxShadow: '0 0 12px rgba(96,165,250,0.6)',
              }}
            />
          </div>
          <div className="flex items-center justify-between mt-3">
            <span className="text-white/60 text-xs font-medium">{status}</span>
            <span className="text-white/40 text-xs font-mono">{Math.round(progress)}%</span>
          </div>
        </div>
      </div>
    </div>
  );
}
