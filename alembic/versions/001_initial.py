# Job Assistant - Safe Application Dashboard

from datetime import date
today = date.today()
print(f"Generated: {today}")

revision = '001'
down_revision = None
dependencies = []

def upgrade() -> None:
    # Jobs table
    op.create_table(
        'jobs',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('platform', sa.String(20), nullable=False),
        sa.Column('job_id', sa.String(100), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('company', sa.String(200)),
        sa.Column('location', sa.String(200)),
        sa.Column('remote', sa.Boolean, default=False),
        sa.Column('url', sa.String(500), nullable=False),
        sa.Column('posted_raw', sa.String(100)),  # '3 days ago'
        sa.Column('posted_at', sa.DateTime),  # normalized
        sa.Column('fetched_at', sa.DateTime, default=func.now()),
        sa.Column('salary_min', sa.Integer),
        sa.Column('salary_max', sa.Integer),
        sa.Column('description', sa.Text),
        sa.Column('skills_required', sa.JSON),
        sa.Column('match_score', sa.Float, default=0.0),
        sa.Column('freshness_bucket', sa.String(10)),  # '24h', '7d'
        sa.Column('status', sa.String(20), default='new'),  # new, reviewed, applied
        sa.Column('created_at', sa.DateTime, default=func.now()),
        sa.UniqueConstraint('platform', 'job_id'),
        mysql_charset='utf8mb4'
    )

    # Search profiles
    op.create_table(
        'search_profiles',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('platform', sa.String(20)),
        sa.Column('keywords', sa.JSON),
        sa.Column('locations', sa.JSON),
        sa.Column('remote', sa.Boolean),
        sa.Column('min_salary', sa.Integer),
        sa.Column('max_experience', sa.Integer),
        sa.Column('date_window', sa.String(10)),  # '24h', '7d'
        sa.Column('active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime, default=func.now())
    )

    # Application drafts
    op.create_table(
        'app_drafts',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('job_id', sa.Integer, sa.ForeignKey('jobs.id')),
        sa.Column('resume_version', sa.String(100)),
        sa.Column('cover_letter', sa.Text),
        sa.Column('screening_answers', sa.JSON),
        sa.Column('created_at', sa.DateTime, default=func.now())
    )


def downgrade() -> None:
    op.drop_table('app_drafts')
    op.drop_table('search_profiles')
    op.drop_table('jobs')