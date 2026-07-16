import { Home, BookOpen, Heart, CalendarDays } from 'lucide-react'

export const NAV_TABS = [
  { path: '/', label: '首頁', Icon: Home },
  { path: '/vocab', label: '單字本', Icon: BookOpen },
  { path: '/favorites', label: '收藏', Icon: Heart },
  { path: '/daily', label: '每日', Icon: CalendarDays },
] as const
