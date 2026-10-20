"""The one subject registry. Subjects configure content and presentation, not behavior."""

DEFAULT_SUBJECT_ID = 'math'
DEFAULTS = (
    ('math', '数学', 'Mathematics', '理解规律，建立数学直觉。', 'Understand patterns and build mathematical intuition.', '#785744', '∑'),
    ('physics', '物理', 'Physics', '从现象出发，探索世界的规律。', 'Explore the laws behind the world around us.', '#456781', '⚛'),
    ('english', '英语', 'English', '用语言连接知识与世界。', 'Connect with knowledge and the world through language.', '#55766c', 'Aa'),
    ('chinese', '语文', 'Chinese', '在阅读与表达中理解与思考。', 'Read, reflect and express ideas with clarity.', '#915847', '文'),
    ('biology', '生物', 'Biology', '认识生命的结构与变化。', 'Discover the structures and processes of life.', '#647746', '❧'),
    ('chemistry', '化学', 'Chemistry', '探索物质的性质与变化。', 'Explore matter, its properties and transformations.', '#796080', '⚗'),
    ('cs', '计算机科学', 'Computer Science', '理解计算，构建解决问题的方法。', 'Understand computation and build ways to solve problems.', '#087f83', 'CS'),
)
SUBJECT_IDS = frozenset(row[0] for row in DEFAULTS)
