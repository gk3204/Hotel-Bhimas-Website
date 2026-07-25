import React from "react";

// Catches render/runtime errors from any routed page so a single component crash degrades to a
// readable error card (nav/header stay put) instead of blanking the whole app. Keyed by route path
// so navigating to a different, working page automatically clears a previous page's error.
class RouteErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Surface it for debugging; the UI below is what the user sees.
    console.error("Page crashed:", error, info?.componentStack);
  }

  handleReset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="max-w-2xl mx-auto mt-10 bg-slate-800/60 border border-red-500/40 rounded-2xl p-8 text-center shadow-xl">
        <div className="text-5xl mb-4">⚠️</div>
        <h2 className="text-2xl font-bold text-white mb-2">This page hit an error</h2>
        <p className="text-slate-400 mb-1">
          Something went wrong while rendering this screen. The rest of the app is still working —
          use the menu to go elsewhere, or try again.
        </p>
        <p className="text-red-300/80 text-xs mb-6 font-mono break-words">
          {this.state.error?.message || String(this.state.error)}
        </p>
        <div className="flex gap-3 justify-center">
          <button
            onClick={this.handleReset}
            className="bg-gradient-to-r from-[#E5C07B] to-[#D4AF37] text-slate-900 font-bold px-5 py-2.5 rounded-lg transition hover:scale-105"
          >
            Try again
          </button>
          <button
            onClick={() => window.location.reload()}
            className="bg-slate-700 hover:bg-slate-600 text-white font-semibold px-5 py-2.5 rounded-lg transition"
          >
            Reload page
          </button>
        </div>
      </div>
    );
  }
}

export default RouteErrorBoundary;
