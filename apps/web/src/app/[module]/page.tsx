import { notFound } from "next/navigation";
import { PageHeading } from "@/components/ui";
import { MODULES, moduleBySlug } from "@/lib/modules";

export function generateStaticParams() {
  return MODULES.filter((m) => m.phase > 1).map((m) => ({ module: m.slug }));
}

export const dynamicParams = false;

export default async function PlannedModulePage({ params }: PageProps<"/[module]">) {
  const { module: slug } = await params;
  const workspaceModule = moduleBySlug(slug);
  if (!workspaceModule || workspaceModule.phase === 1) notFound();
  const Icon = workspaceModule.icon;

  return (
    <>
      <PageHeading kicker={`PLANNED · PHASE ${workspaceModule.phase}`} title={workspaceModule.label}
        subtitle={workspaceModule.summary} />
      <section className="planned-module panel">
        <span className="planned-icon"><Icon size={22} /></span>
        <div>
          <span className="section-kicker">NOT YET BUILT</span>
          <h2>This module arrives in Phase {workspaceModule.phase}.</h2>
          <p>
            It is intentionally empty: the platform does not show sample, simulated, or placeholder
            financial data. When it ships it will include:
          </p>
          <ul>
            {workspaceModule.planned.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      </section>
    </>
  );
}
