-- Migration: qualifier_leads tablosuna duplicate_of kolonu ekle
-- Bu kolon, aynı telefon numarası ile gelen lead'lerin duplicate olarak işaretlenmesini sağlar.

ALTER TABLE qualifier_leads
  ADD COLUMN IF NOT EXISTS duplicate_of UUID REFERENCES qualifier_leads(id) ON DELETE SET NULL;
