import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';
import 'mail_list_screen.dart';
import 'calendar_screen.dart';
import 'mail_approval_screen.dart';

class InboxHub extends StatefulWidget {
  const InboxHub({super.key});
  @override
  State<InboxHub> createState() => _InboxHubState();
}

class _InboxHubState extends State<InboxHub> {
  Map<String, dynamic> _counts = {};
  int _draftCount = 0;

  @override
  void initState() {
    super.initState();
    _loadBadges();
  }

  Future<void> _loadBadges() async {
    try {
      final results = await Future.wait([
        Api.instance.get('/api/counts'),
        Api.instance.get('/api/mail-drafts'),
      ]);
      if (!mounted) return;
      setState(() {
        _counts = (results[0] as Map?)?.cast<String, dynamic>() ?? {};
        _draftCount = ((results[1] as Map?)?['drafts'] as List?)?.length ?? 0;
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
          Text('Inbox', style: TextStyle(color: c.text, fontSize: 24, fontWeight: FontWeight.w800)),
          const SizedBox(height: 4),
          Text('Mails, Termine & Freigaben', style: TextStyle(color: c.dim, fontSize: 13)),
          const SizedBox(height: 20),
          GridView.count(
            crossAxisCount: 3,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 12,
            crossAxisSpacing: 12,
            childAspectRatio: 0.95,
            children: [
              IconTile(icon: Icons.mail_outline, label: 'Posteingang', color: warn, badge: _counts['inbox'] as int?,
                  onTap: () => _open(const MailListScreen())),
              IconTile(icon: Icons.event_outlined, label: 'Kalender', color: accent,
                  onTap: () => _open(const CalendarScreen())),
              IconTile(icon: Icons.rate_review_outlined, label: 'Mail-Freigabe', color: good, badge: _draftCount,
                  onTap: () => _open(const MailApprovalScreen())),
            ],
          ),
        ],
      ),
    );
  }
}
