import { Alert, Divider, Group, Paper, Text, Timeline, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getProcedureTimeline, type LifeEvent } from "../api/client";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";

function procedureDateParts(localTime: string): { year: string; monthDay: string } {
  const datePart = localTime.split("T")[0] ?? localTime;
  const parsed = new Date(`${datePart}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return { year: "", monthDay: datePart };
  return {
    year: parsed.toLocaleDateString("en-US", { year: "numeric" }),
    monthDay: parsed.toLocaleDateString("en-US", { month: "long", day: "numeric" }),
  };
}

interface YearGroup {
  year: string;
  items: LifeEvent[];
}

function groupByYear(ordered: LifeEvent[]): YearGroup[] {
  const groups: YearGroup[] = [];
  for (const item of ordered) {
    const year = procedureDateParts(item.time.local_time).year;
    const current = groups.at(-1);
    if (current?.year === year) current.items.push(item);
    else groups.push({ year, items: [item] });
  }
  return groups;
}

export function ProceduresPage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const procedures = useQuery({
    queryKey: ["procedure-timeline", timezone],
    queryFn: () => getProcedureTimeline(timezone),
  });
  const items = procedures.data?.items ?? [];
  const ordered = [...items].sort((a, b) => a.time.occurred_at.localeCompare(b.time.occurred_at));

  return (
    <Page
      title="Procedures"
      description="Surgical and procedural history, recorded as life-event facts for context alongside the curve."
    >
      <Text c="dimmed" size="sm">
        Stored as life events (category &ldquo;Surgery / procedure&rdquo;). Add a new one from the Life events form
        on the <Link to="/symptoms-diary">Symptoms &amp; Meals</Link> page.
      </Text>
      {procedures.isPending ? <Text mt="lg" role="status">Loading procedure history…</Text> : null}
      {procedures.isError ? (
        <Alert color="red" mt="lg" role="alert">The procedure history could not be loaded.</Alert>
      ) : null}
      {procedures.data !== undefined && ordered.length === 0 ? (
        <Paper component="section" className="empty-state" withBorder radius="lg" p="lg" mt="lg">
          <Title order={2}>No procedures recorded yet</Title>
          <Text mt="xs">
            Add one from the Life events form on the <Link to="/symptoms-diary">Symptoms &amp; Meals</Link> page,
            category &ldquo;Surgery / procedure.&rdquo;
          </Text>
        </Paper>
      ) : null}
      {ordered.length === 0 ? null : (
        <Paper withBorder radius="lg" p={{ base: "md", sm: "lg" }} mt="lg">
          {groupByYear(ordered).map((group, groupIndex) => (
            <div key={group.year}>
              <Group gap="sm" mt={groupIndex === 0 ? 0 : "xl"} mb="sm" wrap="nowrap">
                <Title order={3} c="var(--hc-color-brand-navy, #113f73)" style={{ fontVariantNumeric: "tabular-nums" }}>
                  {group.year}
                </Title>
                <Divider flex={1} />
              </Group>
              <Timeline active={group.items.length} bulletSize={14} lineWidth={2}>
                {group.items.map((item) => (
                  <Timeline.Item key={item.id} title={item.title}>
                    <Text size="sm" fw={600}>{procedureDateParts(item.time.local_time).monthDay}</Text>
                    {item.description === null ? null : <Text size="sm" c="dimmed">{item.description}</Text>}
                    {item.is_sensitive ? (
                      <Text size="xs" c="dimmed" mt={4}>Marked sensitive — hidden from reports and default views.</Text>
                    ) : null}
                  </Timeline.Item>
                ))}
              </Timeline>
            </div>
          ))}
        </Paper>
      )}
    </Page>
  );
}
