-- The interface is in English and French only: accounts that chose Moroccan Darija ('ary') read it in English now.
UPDATE users SET language = 'en' WHERE language NOT IN ('', 'en', 'fr');
