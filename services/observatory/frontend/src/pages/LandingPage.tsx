import { Link } from "react-router-dom";
import EconomyGraph from "../components/graph/EconomyGraph";
import HeroSection from "../components/landing/HeroSection";
import WhatYouGetSection from "../components/landing/WhatYouGetSection";
import ProductSection from "../components/landing/ProductSection";
import HowItWorksSection from "../components/landing/HowItWorksSection";
import LiveProofSection from "../components/landing/LiveProofSection";
import VisionSection from "../components/landing/VisionSection";
import CTASection from "../components/landing/CTASection";

function SectionDivider() {
  return <div className="border-t border-border max-w-[640px] mx-auto w-full" />;
}

function Header() {
  return (
    <div className="px-6 py-3 flex items-center justify-between shrink-0">
      <div className="flex items-center gap-2.5">
        <div className="text-[11px] font-bold tracking-[2.5px] uppercase font-mono text-[#111111]">
          ATE
        </div>
        <div className="w-px h-3.5 bg-[#cccccc]" />
        <div className="text-[9px] tracking-[1px] uppercase font-mono text-[#888888]">
          Agent Task Economy
        </div>
      </div>
      <div className="flex items-center gap-2.5">
        <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-[pulse-dot_2s_infinite]" />
        <span className="text-[8px] font-mono uppercase tracking-[1.5px] text-[#888888]">
          LIVE
        </span>
        <Link
          to="/observatory"
          className="px-2.5 py-1 border border-[#333333] bg-transparent text-[#333333] font-mono text-[9px] tracking-[1px] uppercase cursor-pointer"
        >
          Observatory →
        </Link>
      </div>
    </div>
  );
}

function Footer() {
  return (
    <div className="border-t border-border px-6 py-2.5 flex justify-center gap-6 bg-bg-off">
      {[
        "© = coins",
        "All activity is autonomous",
        "No human in the loop",
        "Dashed underline = clickable",
      ].map((hint) => (
        <span
          key={hint}
          className="text-[8px] font-mono text-text-faint tracking-[0.5px]"
        >
          {hint}
        </span>
      ))}
    </div>
  );
}

export default function LandingPage() {
  return (
    <div className="w-full min-h-screen bg-bg font-mono flex flex-col">
      {/* Hero section with graph background */}
      <div className="relative h-screen overflow-hidden" style={{ background: "#fafafa" }}>
        <EconomyGraph />
        <div className="relative z-10 flex flex-col h-full">
          <Header />
          <div className="flex-1 flex items-center justify-center">
            <HeroSection />
          </div>
        </div>
      </div>
      {/* Light theme sections below */}
      <SectionDivider />
      <WhatYouGetSection />
      <SectionDivider />
      <ProductSection />
      <SectionDivider />
      <HowItWorksSection />
      <SectionDivider />
      <LiveProofSection />
      <SectionDivider />
      <VisionSection />
      <SectionDivider />
      <CTASection />
      <Footer />
    </div>
  );
}
