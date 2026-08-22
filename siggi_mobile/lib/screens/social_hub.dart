import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';
import 'reels_screen.dart';
import 'flyer_screen.dart';
import 'linkedin_screen.dart';
import 'topics_screen.dart';
import 'reach_screen.dart';

/// Landing screen for the "Social" tab - a small icon grid that opens into
/// each content pipeline, instead of stacking everything on one screen.
class SocialHub extends StatefulWidget {
  const SocialHub({super.key});
  @override
  State<SocialHub> createState() => _SocialHubState();
}

class _SocialHubState extends State<SocialHub> {
  int _reelsPending = 0;
  int _flyerPending = 0;
  int _linkedinPending = 0;

  @override
  void initState() {
    super.initState();
    _loadBadges();
  }

  Future<void> _loadBadges() async {
    try {
      final results = await Future.wait([
        Api.instance.get('/api/instagram/reels/pending'),
        Api.instance.get('/api/instagram/flyer-pipeline/pending'),
        Api.instance.get('/api/linkedin/pipeline/drafts?status=pending'),
      ]);
      if (!mounted) return;
      setState(() {
        _reelsPending = ((results[0] as List?) ?? []).length;
        _flyerPending = ((results[1] as List?) ?? []).length;
        _linkedinPending = ((results[2] as List?) ?? []).length;
      });
    } catch (_) {}
  }

  Future<void> _open(Widget screen) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen));
    _loadBadges();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return RefreshIndicator(
      onRefresh: _loadBadges,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('Social', style: TextStyle(color: c.text, fontSize: 24, fontWeight: FontWeight.w800)),
          const SizedBox(height: 4),
          Text('Inhalte erzeugen & freigeben', style: TextStyle(color: c.dim, fontSize: 13)),
          const SizedBox(height: 20),
          GridView.count(
            crossAxisCount: 3,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 12,
            crossAxisSpacing: 12,
            childAspectRatio: 0.95,
            children: [
              IconTile(icon: Icons.movie_creation_outlined, label: 'Reels', color: good, badge: _reelsPending,
                  onTap: () => _open(const ReelsScreen())),
              IconTile(icon: Icons.image_outlined, label: 'Bilder', color: accent, badge: _flyerPending,
                  onTap: () => _open(const FlyerScreen())),
              IconTile(icon: Icons.business_center_outlined, label: 'LinkedIn', color: accent2, badge: _linkedinPending,
                  onTap: () => _open(const LinkedinScreen())),
              IconTile(icon: Icons.lightbulb_outline, label: 'Themen', color: warn,
                  onTap: () => _open(const TopicsScreen())),
              IconTile(icon: Icons.trending_up, label: 'Reichweite', color: const Color(0xFFe1306c),
                  onTap: () => _open(const ReachScreen())),
            ],
          ),
        ],
      ),
    );
  }
}
