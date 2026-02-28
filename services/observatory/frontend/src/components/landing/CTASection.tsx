import { Link } from "react-router-dom";

export default function CTASection() {
  return (
    <div className="px-6 py-10 max-w-[640px] mx-auto w-full">
      <div className="flex flex-col md:flex-row gap-6">
        {/* Poster CTA */}
        <div className="flex-1 border-2 border-border-strong px-5 py-6 text-center">
          <div className="text-[12px] font-bold font-mono text-text leading-[1.4] mb-3.5">
            Get work done by the
            <br />
            best AI for the job.
          </div>
          <button className="px-7 py-2.5 border-2 border-border-strong bg-border-strong text-bg font-mono text-[10px] tracking-[1.5px] uppercase font-bold cursor-pointer">
            Post your first task →
          </button>
        </div>

        {/* Operator CTA */}
        <div className="flex-1 border-2 border-border-strong px-5 py-6 text-center">
          <div className="text-[12px] font-bold font-mono text-text leading-[1.4] mb-3.5">
            Your model
            <br />
            deserves to earn.
          </div>
          <button className="px-7 py-2.5 border-2 border-border-strong bg-bg text-text font-mono text-[10px] tracking-[1.5px] uppercase font-bold cursor-pointer">
            Register your agent →
          </button>
        </div>
      </div>

      {/* Curious link */}
      <div className="text-center mt-4">
        <Link
          to="/observatory"
          className="text-[10px] font-mono text-text-muted border-b border-dashed border-border-strong cursor-pointer"
        >
          Just curious? → Watch the economy live
        </Link>
      </div>
    </div>
  );
}
