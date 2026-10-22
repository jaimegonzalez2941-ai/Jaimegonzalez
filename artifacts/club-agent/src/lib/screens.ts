import {
  LayoutDashboard, Settings2, CalendarDays, PlaneTakeoff, ClipboardCheck, Radar, ScrollText,
  UserPlus, Target, ListChecks, Newspaper, History, type LucideIcon,
} from 'lucide-react';

export interface ScreenDef {
  key: string;
  label: string;
  blurb: string;
  icon: LucideIcon;
}

export const SCREENS: ScreenDef[] = [
  { key: 'overview', label: 'Overview', blurb: 'Saved settings, upcoming events, pending leave, recent audit.', icon: LayoutDashboard },
  { key: 'setup', label: 'Setup', blurb: 'Diagnostics, stage roles, promotion standard, notifications.', icon: Settings2 },
  { key: 'schedule', label: 'Schedule', blurb: 'Events, rosters and assignments.', icon: CalendarDays },
  { key: 'availability', label: 'Availability', blurb: 'Roster availability and leave review.', icon: PlaneTakeoff },
  { key: 'attendance', label: 'Attendance', blurb: 'Marked sessions, reports and promotion review.', icon: ClipboardCheck },
  { key: 'scouting', label: 'Scouting', blurb: 'Dossiers, history, stats and compatible comparisons.', icon: Radar },
  { key: 'evaluations', label: 'Evaluations', blurb: 'Your drafts, five-category rubric, explicit save.', icon: ScrollText },
  { key: 'recruitment', label: 'Recruitment', blurb: 'Shortlist and tier assignment.', icon: UserPlus },
  { key: 'goals', label: 'Goals', blurb: 'Staff development goals.', icon: Target },
  { key: 'tasks', label: 'Tasks', blurb: 'Staff task list.', icon: ListChecks },
  { key: 'briefing', label: 'Briefing', blurb: 'On-demand briefing from saved records.', icon: Newspaper },
  { key: 'audit', label: 'Audit', blurb: 'Who changed what, and when.', icon: History },
];

export const SCREEN_KEYS = SCREENS.map((s) => s.key);

const GROUP_LABELS: Record<string, string> = {
  draft: 'Evaluation drafts',
  scout: 'Scouting',
  schedule: 'Scheduling',
  availability: 'Availability and leave',
  attendance: 'Attendance',
  promotion: 'Promotion',
  shortlist: 'Shortlist',
  recruit: 'Recruitment',
  recruitment: 'Recruitment',
  development: 'Staff goals',
  goal: 'Staff goals',
  assistant: 'Tasks and briefing',
  task: 'Tasks and briefing',
  setup: 'Setup',
  notifications: 'Notifications',
};

export function groupLabel(opId: string): { key: string; label: string } {
  const key = opId.split(/[.:/]/)[0] || 'other';
  return { key, label: GROUP_LABELS[key] ?? key.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase()) };
}
