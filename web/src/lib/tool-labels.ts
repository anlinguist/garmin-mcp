// Coach-friendly labels for tool calls (Dolly shows raw tool names; Rabbit's
// users aren't developers). Raw params/results stay behind the expand toggle.

const METHOD_LABELS: Record<string, string> = {
  get_sleep_data: 'Checked your sleep',
  get_hrv_data: 'Checked your HRV',
  get_training_readiness: 'Checked training readiness',
  get_training_status: 'Checked training status',
  get_body_battery: 'Checked body battery',
  get_activity: 'Opened an activity',
  get_activity_splits: 'Looked at the splits',
  get_activities: 'Looked at your activities',
  get_activities_by_date: 'Looked at your activities',
  get_user_summary: 'Checked your daily summary',
  get_stats: 'Checked your daily summary',
  get_rhr_day: 'Checked resting heart rate',
  get_race_predictions: 'Checked race predictions',
  get_max_metrics: 'Checked VO2 max',
  get_endurance_score: 'Checked endurance score',
}

function shortDate(s: unknown): string {
  if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return ''
  const d = new Date(`${s}T12:00:00`)
  return ` (${d.toLocaleDateString([], { month: 'short', day: 'numeric' })})`
}

export function toolLabel(toolName: string, params: Record<string, unknown>): string {
  if (toolName === 'recent_activities') {
    const days = typeof params.days === 'number' ? params.days : 7
    const sport = typeof params.sport === 'string' && params.sport ? ` ${params.sport}` : ''
    return `Looked at your last ${days} days of${sport} activities`
  }
  if (toolName === 'training_history') {
    const weeks = typeof params.weeks === 'number' ? params.weeks : 16
    return `Reviewed your last ${weeks} weeks of training`
  }
  if (toolName === 'search') return 'Looked up what Garmin data is available'
  if (toolName === 'update_notes') return 'Updated my coach notes'
  if (toolName === 'update_athlete_profile') return 'Noted something about you'
  if (toolName === 'save_plan') return 'Saved your plan'
  if (toolName === 'week_plan') return params.week === 'next' ? "Looked at next week's plan" : "Looked at this week's plan"
  if (toolName === 'change_day') {
    const d = typeof params.date === 'string' ? shortDate(params.date).replace(/[()]/g, '').trim() : ''
    return params.reset ? `Put ${d || 'the day'} back to the plan` : `Changed ${d || 'a day'} in your plan`
  }
  if (toolName === 'read_file') return 'Reopened your file'
  if (toolName === 'render_chart') return 'Drawing a chart'
  if (toolName === 'save_goal') return params.replace_current ? 'Started a new goal' : 'Saved your goal'
  if (toolName === 'execute') {
    const method = String(params.method ?? '')
    const args = (params.args ?? {}) as Record<string, unknown>
    const base = METHOD_LABELS[method] ?? `Checked ${method.replace(/^get_/, '').replace(/_/g, ' ')}`
    return base + shortDate(args.cdate ?? args.startdate)
  }
  return toolName.replace(/_/g, ' ')
}
