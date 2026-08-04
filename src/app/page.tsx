import { Hero } from "@/components/landing/Hero";
import { SiteFooter } from "@/components/landing/SiteFooter";
import { SiteHeader } from "@/components/landing/SiteHeader";
import { StrategiesPreview } from "@/components/landing/StrategiesPreview";
import { SystemSection } from "@/components/landing/SystemSection";
import { WorkflowSection } from "@/components/landing/WorkflowSection";

export default function Home() {
  return (
    <main className="flex-1">
      <SiteHeader />
      <Hero />
      <SystemSection />
      <StrategiesPreview />
      <WorkflowSection />
      <SiteFooter />
    </main>
  );
}
